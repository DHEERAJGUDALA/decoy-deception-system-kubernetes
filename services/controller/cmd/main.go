package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/client-go/kubernetes"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	"sigs.k8s.io/controller-runtime/pkg/source"
)

// AppGraph CRD
type AppGraph struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`
	Spec              AppGraphSpec   `json:"spec,omitempty"`
	Status            AppGraphStatus `json:"status,omitempty"`
}

type AppGraphSpec struct {
	Services            []string `json:"services"`
	DecoyCount          int      `json:"decoyCount"`
	AutoCleanupMinutes  int      `json:"autoCleanupMinutes"`
	SourceIP            string   `json:"sourceIP"`
	AttackType          string   `json:"attackType"`
	Severity            string   `json:"severity,omitempty"`
}

type AppGraphStatus struct {
	Phase               string    `json:"phase,omitempty"`
	DecoyPods           []string  `json:"decoyPods,omitempty"`
	DecoyURLs           []string  `json:"decoyURLs,omitempty"`
	CreatedAt           string    `json:"createdAt,omitempty"`
	CleanupScheduledAt  string    `json:"cleanupScheduledAt,omitempty"`
	Message             string    `json:"message,omitempty"`
}

type AppGraphList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []AppGraph `json:"items"`
}

func (a *AppGraph) DeepCopyObject() runtime.Object {
	return a.DeepCopy()
}

func (a *AppGraph) DeepCopy() *AppGraph {
	if a == nil {
		return nil
	}
	out := new(AppGraph)
	*out = *a
	return out
}

func (a *AppGraphList) DeepCopyObject() runtime.Object {
	return a.DeepCopyList()
}

func (a *AppGraphList) DeepCopyList() *AppGraphList {
	if a == nil {
		return nil
	}
	out := new(AppGraphList)
	*out = *a
	return out
}

var (
	GroupVersion = schema.GroupVersion{Group: "deception.k8s.io", Version: "v1"}
	SchemeBuilder = runtime.NewSchemeBuilder(addKnownTypes)
	AddToScheme = SchemeBuilder.AddToScheme
)

func addKnownTypes(scheme *runtime.Scheme) error {
	scheme.AddKnownTypes(GroupVersion,
		&AppGraph{},
		&AppGraphList{},
	)
	metav1.AddToGroupVersion(scheme, GroupVersion)
	return nil
}

// Alert from Sentinel
type Alert struct {
	Timestamp   string   `json:"timestamp"`
	AttackType  string   `json:"attack_type"`
	SourceIP    string   `json:"source_ip"`
	Evidence    string   `json:"evidence"`
	Severity    string   `json:"severity"`
	PodName     string   `json:"pod_name"`
	DecoyURLs   []string `json:"decoy_urls,omitempty"`
}

// WebSocket Event
type WSEvent struct {
	Type      string                 `json:"type"`
	Timestamp string                 `json:"timestamp"`
	Data      map[string]interface{} `json:"data"`
}

// Controller
type AppGraphController struct {
	client.Client
	Clientset      *kubernetes.Clientset
	Scheme         *runtime.Scheme
	ManagerURL     string
	Namespace      string
	wsClients      map[*websocket.Conn]bool
	wsClientsMu    sync.RWMutex
	wsBroadcast    chan WSEvent
}

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

func (c *AppGraphController) broadcastEvent(eventType string, data map[string]interface{}) {
	event := WSEvent{
		Type:      eventType,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
		Data:      data,
	}
	
	select {
	case c.wsBroadcast <- event:
	default:
	}
}

func (c *AppGraphController) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[ERROR] WebSocket upgrade failed: %v", err)
		return
	}
	defer conn.Close()

	c.wsClientsMu.Lock()
	c.wsClients[conn] = true
	c.wsClientsMu.Unlock()

	defer func() {
		c.wsClientsMu.Lock()
		delete(c.wsClients, conn)
		c.wsClientsMu.Unlock()
	}()

	log.Printf("[WS] Client connected: %s", conn.RemoteAddr())

	// Keep connection alive
	for {
		_, _, err := conn.ReadMessage()
		if err != nil {
			break
		}
	}
}

func (c *AppGraphController) broadcastWorker() {
	for event := range c.wsBroadcast {
		c.wsClientsMu.RLock()
		for conn := range c.wsClients {
			err := conn.WriteJSON(event)
			if err != nil {
				log.Printf("[WS] Send error: %v", err)
			}
		}
		c.wsClientsMu.RUnlock()
	}
}

func (c *AppGraphController) Reconcile(ctx context.Context, req reconcile.Request) (reconcile.Result, error) {
	log.Printf("[RECONCILE] Processing AppGraph: %s", req.NamespacedName)

	var ag AppGraph
	if err := c.Get(ctx, req.NamespacedName, &ag); err != nil {
		if errors.IsNotFound(err) {
			return reconcile.Result{}, nil
		}
		return reconcile.Result{}, err
	}

	// Initialize status if needed
	if ag.Status.Phase == "" {
		ag.Status.Phase = "Pending"
		ag.Status.CreatedAt = time.Now().UTC().Format(time.RFC3339)
		cleanupTime := time.Now().Add(time.Duration(ag.Spec.AutoCleanupMinutes) * time.Minute)
		ag.Status.CleanupScheduledAt = cleanupTime.Format(time.RFC3339)
		if err := c.Status().Update(ctx, &ag); err != nil {
			return reconcile.Result{}, err
		}
	}

	// Check if cleanup time reached
	if ag.Status.CleanupScheduledAt != "" {
		cleanupTime, _ := time.Parse(time.RFC3339, ag.Status.CleanupScheduledAt)
		if time.Now().After(cleanupTime) {
			log.Printf("[CLEANUP] Auto-cleanup triggered for %s", ag.Name)
			c.broadcastEvent("cleanup", map[string]interface{}{
				"name":      ag.Name,
				"source_ip": ag.Spec.SourceIP,
			})
			return reconcile.Result{}, c.Delete(ctx, &ag)
		}
	}

	// Create decoys if pending
	if ag.Status.Phase == "Pending" {
		ag.Status.Phase = "Creating"
		if err := c.Status().Update(ctx, &ag); err != nil {
			return reconcile.Result{}, err
		}

		if err := c.createDecoys(ctx, &ag); err != nil {
			ag.Status.Phase = "Failed"
			ag.Status.Message = err.Error()
			c.Status().Update(ctx, &ag)
			return reconcile.Result{}, err
		}

		ag.Status.Phase = "Active"
		ag.Status.Message = fmt.Sprintf("Deployed %d decoys", len(ag.Status.DecoyPods))
		if err := c.Status().Update(ctx, &ag); err != nil {
			return reconcile.Result{}, err
		}

		// Call Manager to block IP
		if err := c.blockIPInManager(ag.Spec.SourceIP, ag.Status.DecoyURLs); err != nil {
			log.Printf("[ERROR] Failed to block IP in Manager: %v", err)
		}

		c.broadcastEvent("decoys_created", map[string]interface{}{
			"name":       ag.Name,
			"source_ip":  ag.Spec.SourceIP,
			"decoy_urls": ag.Status.DecoyURLs,
			"count":      len(ag.Status.DecoyPods),
		})
	}

	// Requeue for cleanup check
	nextCheck := time.Minute
	if ag.Status.CleanupScheduledAt != "" {
		cleanupTime, _ := time.Parse(time.RFC3339, ag.Status.CleanupScheduledAt)
		untilCleanup := time.Until(cleanupTime)
		if untilCleanup > 0 && untilCleanup < nextCheck {
			nextCheck = untilCleanup
		}
	}

	return reconcile.Result{RequeueAfter: nextCheck}, nil
}

func (c *AppGraphController) createDecoys(ctx context.Context, ag *AppGraph) error {
	decoyTypes := []string{"exact", "slow", "logger"}
	decoyPods := []string{}
	decoyURLs := []string{}

	for i, decoyType := range decoyTypes {
		podName := fmt.Sprintf("decoy-%s-%s-%d", ag.Name, ag.Spec.SourceIP[:min(8, len(ag.Spec.SourceIP))], i+1)
		
		// Create Pod
		pod := &corev1.Pod{
			ObjectMeta: metav1.ObjectMeta{
				Name:      podName,
				Namespace: c.Namespace,
				Labels: map[string]string{
					"app":          "decoy",
					"appgraph":     ag.Name,
					"decoy-type":   decoyType,
					"source-ip":    ag.Spec.SourceIP,
					"attack-type":  ag.Spec.AttackType,
				},
			},
			Spec: corev1.PodSpec{
				Containers: []corev1.Container{
					{
						Name:            "decoy",
						Image:           "frontend-api:latest",
						ImagePullPolicy: corev1.PullIfNotPresent,
						Env: []corev1.EnvVar{
							{Name: "IS_DECOY", Value: "true"},
							{Name: "DECOY_TYPE", Value: decoyType},
							{Name: "DECOY_LATENCY", Value: getLatency(decoyType)},
							{Name: "DECOY_LOGGING", Value: getLogging(decoyType)},
						},
						Resources: corev1.ResourceRequirements{
							Requests: corev1.ResourceList{
								corev1.ResourceMemory: resource.MustParse("40Mi"),
								corev1.ResourceCPU:    resource.MustParse("20m"),
							},
							Limits: corev1.ResourceList{
								corev1.ResourceMemory: resource.MustParse("40Mi"),
								corev1.ResourceCPU:    resource.MustParse("20m"),
							},
						},
					},
				},
			},
		}

		if err := c.Clientset.CoreV1().Pods(c.Namespace).Create(ctx, pod, metav1.CreateOptions{}); err != nil {
			return fmt.Errorf("failed to create pod %s: %v", podName, err)
		}

		decoyPods = append(decoyPods, podName)
		decoyURLs = append(decoyURLs, fmt.Sprintf("http://%s:8080", podName))

		log.Printf("[DECOY] Created %s (%s) for %s", podName, decoyType, ag.Spec.SourceIP)

		// Create NetworkPolicy for isolation
		if err := c.createNetworkPolicy(ctx, podName, ag); err != nil {
			log.Printf("[WARN] Failed to create NetworkPolicy for %s: %v", podName, err)
		}

		// Stagger creation by 0.5s
		if i < len(decoyTypes)-1 {
			time.Sleep(500 * time.Millisecond)
		}
	}

	ag.Status.DecoyPods = decoyPods
	ag.Status.DecoyURLs = decoyURLs

	return nil
}

func (c *AppGraphController) createNetworkPolicy(ctx context.Context, podName string, ag *AppGraph) error {
	np := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("decoy-policy-%s", podName),
			Namespace: c.Namespace,
		},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{
				MatchLabels: map[string]string{
					"app": "decoy",
					"appgraph": ag.Name,
				},
			},
			PolicyTypes: []networkingv1.PolicyType{
				networkingv1.PolicyTypeIngress,
				networkingv1.PolicyTypeEgress,
			},
			Ingress: []networkingv1.NetworkPolicyIngressRule{
				{
					From: []networkingv1.NetworkPolicyPeer{
						{
							PodSelector: &metav1.LabelSelector{
								MatchLabels: map[string]string{
									"app": "manager",
								},
							},
						},
					},
				},
			},
			Egress: []networkingv1.NetworkPolicyEgressRule{
				{
					To: []networkingv1.NetworkPolicyPeer{
						{
							PodSelector: &metav1.LabelSelector{
								MatchLabels: map[string]string{
									"app": "reporter-service",
								},
							},
						},
					},
				},
			},
		},
	}

	_, err := c.Clientset.NetworkingV1().NetworkPolicies(c.Namespace).Create(ctx, np, metav1.CreateOptions{})
	return err
}

func (c *AppGraphController) blockIPInManager(sourceIP string, decoyURLs []string) error {
	payload := map[string]interface{}{
		"source_ip":  sourceIP,
		"decoy_urls": decoyURLs,
	}

	data, _ := json.Marshal(payload)
	resp, err := http.Post(c.ManagerURL+"/api/block_ip", "application/json", bytes.NewBuffer(data))
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("manager returned status %d", resp.StatusCode)
	}

	log.Printf("[MANAGER] Blocked IP %s with %d decoys", sourceIP, len(decoyURLs))
	return nil
}

func (c *AppGraphController) handleAlerts(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var alert Alert
	if err := json.NewDecoder(r.Body).Decode(&alert); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	log.Printf("[ALERT] Received: %s from %s (severity: %s)", alert.AttackType, alert.SourceIP, alert.Severity)

	// Broadcast to dashboard
	c.broadcastEvent("alert", map[string]interface{}{
		"source_ip":   alert.SourceIP,
		"attack_type": alert.AttackType,
		"severity":    alert.Severity,
		"evidence":    alert.Evidence,
	})

	// Create AppGraph
	ag := &AppGraph{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("ag-%s-%d", alert.SourceIP, time.Now().Unix()),
			Namespace: c.Namespace,
		},
		Spec: AppGraphSpec{
			Services:           []string{"frontend-api"},
			DecoyCount:         3,
			AutoCleanupMinutes: 15,
			SourceIP:           alert.SourceIP,
			AttackType:         alert.AttackType,
			Severity:           alert.Severity,
		},
	}

	if err := c.Create(context.Background(), ag); err != nil {
		log.Printf("[ERROR] Failed to create AppGraph: %v", err)
		http.Error(w, "Failed to create AppGraph", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success": true,
		"message": "Alert processed and decoys scheduled",
		"appgraph": ag.Name,
	})
}

func getLatency(decoyType string) string {
	if decoyType == "slow" {
		return "1000"
	}
	return "0"
}

func getLogging(decoyType string) string {
	if decoyType == "logger" {
		return "verbose"
	}
	return "normal"
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func main() {
	log.Println("[CONTROLLER] Starting AppGraph Controller...")

	managerURL := os.Getenv("MANAGER_URL")
	if managerURL == "" {
		managerURL = "http://manager:8080"
	}

	namespace := os.Getenv("NAMESPACE")
	if namespace == "" {
		namespace = "default"
	}

	// Setup controller-runtime
	cfg := ctrl.GetConfigOrDie()
	scheme := runtime.NewScheme()
	_ = corev1.AddToScheme(scheme)
	_ = networkingv1.AddToScheme(scheme)
	_ = AddToScheme(scheme)

	mgr, err := ctrl.NewManager(cfg, ctrl.Options{
		Scheme:    scheme,
		Namespace: namespace,
	})
	if err != nil {
		log.Fatalf("[FATAL] Failed to create manager: %v", err)
	}

	clientset := kubernetes.NewForConfig(cfg)

	agController := &AppGraphController{
		Client:      mgr.GetClient(),
		Clientset:   clientset,
		Scheme:      mgr.GetScheme(),
		ManagerURL:  managerURL,
		Namespace:   namespace,
		wsClients:   make(map[*websocket.Conn]bool),
		wsBroadcast: make(chan WSEvent, 100),
	}

	// Start WebSocket broadcaster
	go agController.broadcastWorker()

	// Setup controller
	c, err := controller.New("appgraph-controller", mgr, controller.Options{
		Reconciler: agController,
	})
	if err != nil {
		log.Fatalf("[FATAL] Failed to create controller: %v", err)
	}

	if err := c.Watch(&source.Kind{Type: &AppGraph{}}, &handler.EnqueueRequestForObject{}); err != nil {
		log.Fatalf("[FATAL] Failed to watch AppGraph: %v", err)
	}

	// HTTP Server for dashboard and alerts
	http.HandleFunc("/api/alerts", agController.handleAlerts)
	http.HandleFunc("/ws", agController.handleWebSocket)
	http.HandleFunc("/", serveDashboard)

	go func() {
		log.Println("[HTTP] Dashboard listening on :8090")
		if err := http.ListenAndServe(":8090", nil); err != nil {
			log.Fatalf("[FATAL] HTTP server failed: %v", err)
		}
	}()

	log.Println("[CONTROLLER] Starting manager...")
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		log.Fatalf("[FATAL] Manager failed: %v", err)
	}
}

func serveDashboard(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html")
	fmt.Fprint(w, dashboardHTML)
}

const dashboardHTML = `<!DOCTYPE html>
<html>
<head>
    <title>Decoy Deception System - Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0a0e27;
            color: #e0e0e0;
            overflow-x: hidden;
        }
        .header {
            background: linear-gradient(135deg, #1a1f3a 0%, #2d3561 100%);
            padding: 20px 40px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        }
        h1 {
            color: #4fc3f7;
            font-size: 28px;
            font-weight: 300;
            letter-spacing: 2px;
        }
        .container {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 20px;
            padding: 20px;
            height: calc(100vh - 100px);
        }
        .panel {
            background: #1a1f3a;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        .panel h2 {
            color: #4fc3f7;
            font-size: 18px;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #2d3561;
        }
        #graph-container {
            height: calc(100% - 50px);
        }
        .metrics {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-bottom: 20px;
        }
        .metric-card {
            background: #2d3561;
            padding: 15px;
            border-radius: 6px;
            border-left: 4px solid #4fc3f7;
        }
        .metric-value {
            font-size: 32px;
            font-weight: bold;
            color: #4fc3f7;
        }
        .metric-label {
            font-size: 12px;
            color: #9e9e9e;
            text-transform: uppercase;
            margin-top: 5px;
        }
        .timeline {
            max-height: 400px;
            overflow-y: auto;
            margin-top: 15px;
        }
        .event {
            background: #2d3561;
            padding: 12px;
            margin-bottom: 10px;
            border-radius: 4px;
            border-left: 3px solid #4fc3f7;
            animation: slideIn 0.3s ease-out;
        }
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        .event-type {
            font-weight: bold;
            color: #4fc3f7;
        }
        .event-time {
            font-size: 11px;
            color: #757575;
        }
        .severity-critical { border-left-color: #f44336; }
        .severity-high { border-left-color: #ff9800; }
        .severity-medium { border-left-color: #ffeb3b; }
        .node {
            cursor: pointer;
        }
        .node circle {
            stroke-width: 2px;
        }
        .node.legitimate circle {
            fill: #4caf50;
            stroke: #66bb6a;
        }
        .node.decoy circle {
            fill: #2196f3;
            stroke: #42a5f5;
        }
        .node.attacker circle {
            fill: #f44336;
            stroke: #ef5350;
        }
        .node text {
            fill: #e0e0e0;
            font-size: 12px;
        }
        .link {
            stroke: #4fc3f7;
            stroke-opacity: 0.6;
            stroke-width: 2px;
        }
        .link.attack {
            stroke: #f44336;
            stroke-dasharray: 5,5;
            animation: dash 1s linear infinite;
        }
        @keyframes dash {
            to { stroke-dashoffset: -10; }
        }
        .status-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 8px;
            animation: pulse 2s infinite;
        }
        .status-active { background: #4caf50; }
        .status-warning { background: #ff9800; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🛡️ Decoy Deception System Dashboard</h1>
    </div>
    <div class="container">
        <div class="panel">
            <h2>Network Graph</h2>
            <div id="graph-container"></div>
        </div>
        <div class="panel">
            <h2>Metrics</h2>
            <div class="metrics">
                <div class="metric-card">
                    <div class="metric-value" id="total-alerts">0</div>
                    <div class="metric-label">Total Alerts</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value" id="active-decoys">0</div>
                    <div class="metric-label">Active Decoys</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value" id="blocked-ips">0</div>
                    <div class="metric-label">Blocked IPs</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value" id="attack-types">0</div>
                    <div class="metric-label">Attack Types</div>
                </div>
            </div>
            <h2>Event Timeline</h2>
            <div class="timeline" id="timeline"></div>
        </div>
    </div>

    <script>
        // WebSocket connection
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        let ws;
        let reconnectInterval = 3000;
        let metrics = { alerts: 0, decoys: 0, blockedIPs: new Set(), attackTypes: new Set() };

        function connectWebSocket() {
            ws = new WebSocket(protocol + '//' + window.location.host + '/ws');
            
            ws.onopen = () => {
                console.log('[WS] Connected');
                addEvent('system', 'Dashboard connected', 'info');
            };
            
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                handleEvent(data);
            };
            
            ws.onclose = () => {
                console.log('[WS] Disconnected, reconnecting...');
                setTimeout(connectWebSocket, reconnectInterval);
            };
            
            ws.onerror = (error) => {
                console.error('[WS] Error:', error);
            };
        }

        function handleEvent(event) {
            console.log('[EVENT]', event);
            
            switch(event.type) {
                case 'alert':
                    metrics.alerts++;
                    metrics.blockedIPs.add(event.data.source_ip);
                    metrics.attackTypes.add(event.data.attack_type);
                    addEvent('alert', 'Attack detected: ' + event.data.attack_type + ' from ' + event.data.source_ip, event.data.severity);
                    addAttackerNode(event.data.source_ip, event.data.attack_type);
                    break;
                case 'decoys_created':
                    metrics.decoys += event.data.count;
                    addEvent('decoy', event.data.count + ' decoys deployed for ' + event.data.source_ip, 'info');
                    addDecoyNodes(event.data.source_ip, event.data.decoy_urls);
                    break;
                case 'cleanup':
                    addEvent('cleanup', 'Cleaned up decoys for ' + event.data.source_ip, 'info');
                    removeDecoyNodes(event.data.source_ip);
                    break;
            }
            
            updateMetrics();
        }

        function updateMetrics() {
            document.getElementById('total-alerts').textContent = metrics.alerts;
            document.getElementById('active-decoys').textContent = metrics.decoys;
            document.getElementById('blocked-ips').textContent = metrics.blockedIPs.size;
            document.getElementById('attack-types').textContent = metrics.attackTypes.size;
        }

        function addEvent(type, message, severity) {
            const timeline = document.getElementById('timeline');
            const event = document.createElement('div');
            event.className = 'event severity-' + severity;
            event.innerHTML = '<div class="event-type">' + type.toUpperCase() + '</div>' +
                            '<div>' + message + '</div>' +
                            '<div class="event-time">' + new Date().toLocaleTimeString() + '</div>';
            timeline.insertBefore(event, timeline.firstChild);
            
            // Keep only last 50 events
            while(timeline.children.length > 50) {
                timeline.removeChild(timeline.lastChild);
            }
        }

        // D3 Graph
        const width = document.getElementById('graph-container').clientWidth;
        const height = document.getElementById('graph-container').clientHeight;

        const svg = d3.select('#graph-container')
            .append('svg')
            .attr('width', width)
            .attr('height', height);

        const g = svg.append('g');

        const zoom = d3.zoom()
            .scaleExtent([0.5, 3])
            .on('zoom', (event) => g.attr('transform', event.transform));
        
        svg.call(zoom);

        let nodes = [
            { id: 'frontend-api', type: 'legitimate', x: width/2, y: height/2 },
        ];
        let links = [];

        const simulation = d3.forceSimulation(nodes)
            .force('link', d3.forceLink(links).id(d => d.id).distance(150))
            .force('charge', d3.forceManyBody().strength(-300))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collision', d3.forceCollide().radius(40));

        function updateGraph() {
            const link = g.selectAll('.link')
                .data(links, d => d.source.id + '-' + d.target.id);
            
            link.exit().remove();
            
            const linkEnter = link.enter()
                .append('line')
                .attr('class', d => 'link ' + d.type);
            
            const node = g.selectAll('.node')
                .data(nodes, d => d.id);
            
            node.exit().remove();
            
            const nodeEnter = node.enter()
                .append('g')
                .attr('class', d => 'node ' + d.type)
                .call(d3.drag()
                    .on('start', dragStarted)
                    .on('drag', dragged)
                    .on('end', dragEnded));
            
            nodeEnter.append('circle')
                .attr('r', d => d.type === 'attacker' ? 15 : 20);
            
            nodeEnter.append('text')
                .attr('dy', 30)
                .attr('text-anchor', 'middle')
                .text(d => d.label || d.id);
            
            simulation.nodes(nodes);
            simulation.force('link').links(links);
            simulation.alpha(1).restart();
            
            simulation.on('tick', () => {
                g.selectAll('.link')
                    .attr('x1', d => d.source.x)
                    .attr('y1', d => d.source.y)
                    .attr('x2', d => d.target.x)
                    .attr('y2', d => d.target.y);
                
                g.selectAll('.node')
                    .attr('transform', d => 'translate(' + d.x + ',' + d.y + ')');
            });
        }

        function addAttackerNode(ip, attackType) {
            if (!nodes.find(n => n.id === ip)) {
                nodes.push({ id: ip, type: 'attacker', label: ip + '\\n(' + attackType + ')' });
                links.push({ source: ip, target: 'frontend-api', type: 'attack' });
                updateGraph();
            }
        }

        function addDecoyNodes(ip, decoyURLs) {
            decoyURLs.forEach((url, i) => {
                const decoyId = 'decoy-' + ip + '-' + i;
                if (!nodes.find(n => n.id === decoyId)) {
                    nodes.push({ id: decoyId, type: 'decoy', label: 'Decoy ' + (i+1) });
                    links.push({ source: ip, target: decoyId, type: 'redirect' });
                }
            });
            updateGraph();
        }

        function removeDecoyNodes(ip) {
            nodes = nodes.filter(n => !n.id.startsWith('decoy-' + ip));
            links = links.filter(l => !l.target.id || !l.target.id.startsWith('decoy-' + ip));
            metrics.decoys = Math.max(0, metrics.decoys - 3);
            updateGraph();
        }

        function dragStarted(event, d) {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
        }

        function dragged(event, d) {
            d.fx = event.x;
            d.fy = event.y;
        }

        function dragEnded(event, d) {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
        }

        // Initialize
        connectWebSocket();
        updateGraph();
    </script>
</body>
</html>`
