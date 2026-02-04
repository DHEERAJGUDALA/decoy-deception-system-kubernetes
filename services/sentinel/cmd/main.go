package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/cache"
)

type Config struct {
	ControllerURL        string
	Namespace            string
	WatchLabels          string
	SQLiPatterns         []string
	PathTraversalPattern string
	RateLimitThreshold   int
	RateLimitWindow      time.Duration
	AuthFailureLimit     int
	AuthFailureWindow    time.Duration
	CooldownPeriod       time.Duration
}

type Alert struct {
	Timestamp   string   `json:"timestamp"`
	AttackType  string   `json:"attack_type"`
	SourceIP    string   `json:"source_ip"`
	Evidence    string   `json:"evidence"`
	Severity    string   `json:"severity"`
	PodName     string   `json:"pod_name"`
	DecoyURLs   []string `json:"decoy_urls,omitempty"`
}

type AttackerState struct {
	RequestCount   int
	AuthFailures   int
	LastSeen       time.Time
	FirstSeen      time.Time
	LastAlertTime  time.Time
	AlertsSent     int
}

type Sentinel struct {
	config          Config
	clientset       *kubernetes.Clientset
	sqliPatterns    []*regexp.Regexp
	pathTraversal   *regexp.Regexp
	attackerStates  map[string]*AttackerState
	mu              sync.RWMutex
}

func loadConfig() Config {
	controllerURL := os.Getenv("CONTROLLER_URL")
	if controllerURL == "" {
		controllerURL = "http://controller:8080/api/alerts"
	}

	namespace := os.Getenv("NAMESPACE")
	if namespace == "" {
		namespace = "default"
	}

	watchLabels := os.Getenv("WATCH_LABELS")
	if watchLabels == "" {
		watchLabels = "app=frontend-api"
	}

	return Config{
		ControllerURL: controllerURL,
		Namespace:     namespace,
		WatchLabels:   watchLabels,
		SQLiPatterns: []string{
			`(?i)(union\s+select|select\s+.*\s+from|insert\s+into|delete\s+from|drop\s+table)`,
			`(?i)(or\s+1\s*=\s*1|'\s*or\s+'1'\s*=\s*'1)`,
			`(?i)(exec\s*\(|execute\s+immediate)`,
			`(?i)(\-\-|;--|\/\*|\*\/)`,
		},
		PathTraversalPattern: `(?i)(\.\.\/|\.\.\\|%2e%2e%2f|%2e%2e\/|\.\.%2f)`,
		RateLimitThreshold:   50,
		RateLimitWindow:      time.Minute,
		AuthFailureLimit:     3,
		AuthFailureWindow:    time.Minute,
		CooldownPeriod:       5 * time.Minute,
	}
}

func NewSentinel(config Config, clientset *kubernetes.Clientset) (*Sentinel, error) {
	s := &Sentinel{
		config:         config,
		clientset:      clientset,
		attackerStates: make(map[string]*AttackerState),
	}

	// Compile regex patterns
	for _, pattern := range config.SQLiPatterns {
		re, err := regexp.Compile(pattern)
		if err != nil {
			return nil, fmt.Errorf("failed to compile SQLi pattern: %v", err)
		}
		s.sqliPatterns = append(s.sqliPatterns, re)
	}

	var err error
	s.pathTraversal, err = regexp.Compile(config.PathTraversalPattern)
	if err != nil {
		return nil, fmt.Errorf("failed to compile path traversal pattern: %v", err)
	}

	return s, nil
}

func (s *Sentinel) getOrCreateAttackerState(ip string) *AttackerState {
	s.mu.Lock()
	defer s.mu.Unlock()

	state, exists := s.attackerStates[ip]
	if !exists {
		state = &AttackerState{
			FirstSeen: time.Now(),
			LastSeen:  time.Now(),
		}
		s.attackerStates[ip] = state
	}
	return state
}

func (s *Sentinel) detectSQLi(logLine string) bool {
	for _, re := range s.sqliPatterns {
		if re.MatchString(logLine) {
			return true
		}
	}
	return false
}

func (s *Sentinel) detectPathTraversal(logLine string) bool {
	return s.pathTraversal.MatchString(logLine)
}

func (s *Sentinel) detectAuthFailure(logLine string) bool {
	// Check for auth failure indicators in logs
	authFailurePatterns := []string{
		"401",
		"unauthorized",
		"authentication failed",
		"invalid credentials",
		"login failed",
	}

	lower := strings.ToLower(logLine)
	for _, pattern := range authFailurePatterns {
		if strings.Contains(lower, pattern) {
			return true
		}
	}
	return false
}

func (s *Sentinel) extractSourceIP(logLine string) string {
	// Parse JSON log to extract source_ip
	var logData map[string]interface{}
	if err := json.Unmarshal([]byte(logLine), &logData); err == nil {
		if ip, ok := logData["source_ip"].(string); ok {
			return ip
		}
	}

	// Fallback: regex to find IP address
	ipPattern := regexp.MustCompile(`\b(?:\d{1,3}\.){3}\d{1,3}\b`)
	if match := ipPattern.FindString(logLine); match != "" {
		return match
	}

	return ""
}

func (s *Sentinel) checkRateLimit(ip string, state *AttackerState) bool {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now()

	// Reset counter if outside window
	if now.Sub(state.FirstSeen) > s.config.RateLimitWindow {
		state.RequestCount = 1
		state.FirstSeen = now
		return false
	}

	state.RequestCount++
	state.LastSeen = now

	return state.RequestCount > s.config.RateLimitThreshold
}

func (s *Sentinel) checkAuthFailures(ip string, state *AttackerState) bool {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now()

	// Reset counter if outside window
	if now.Sub(state.FirstSeen) > s.config.AuthFailureWindow {
		state.AuthFailures = 1
		state.FirstSeen = now
		return false
	}

	state.AuthFailures++
	state.LastSeen = now

	return state.AuthFailures > s.config.AuthFailureLimit
}

func (s *Sentinel) shouldAlert(ip string, state *AttackerState) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()

	// Check cooldown period
	if time.Since(state.LastAlertTime) < s.config.CooldownPeriod {
		return false
	}

	return true
}

func (s *Sentinel) sendAlert(alert Alert) error {
	alertJSON, err := json.Marshal(alert)
	if err != nil {
		return fmt.Errorf("failed to marshal alert: %v", err)
	}

	log.Printf("[ALERT] Sending: %s", string(alertJSON))

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Post(s.config.ControllerURL, "application/json", bytes.NewBuffer(alertJSON))
	if err != nil {
		return fmt.Errorf("failed to send alert: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		return fmt.Errorf("controller returned status %d", resp.StatusCode)
	}

	log.Printf("[ALERT] Sent successfully to controller")
	return nil
}

func (s *Sentinel) processLogLine(logLine, podName string) {
	sourceIP := s.extractSourceIP(logLine)
	if sourceIP == "" {
		return
	}

	state := s.getOrCreateAttackerState(sourceIP)

	var alertType string
	var evidence string
	var severity string

	// Detect SQLi
	if s.detectSQLi(logLine) {
		alertType = "sql_injection"
		evidence = logLine
		severity = "critical"
	} else if s.detectPathTraversal(logLine) {
		alertType = "path_traversal"
		evidence = logLine
		severity = "high"
	} else if s.detectAuthFailure(logLine) {
		if s.checkAuthFailures(sourceIP, state) {
			alertType = "auth_failure_brute_force"
			evidence = fmt.Sprintf("Multiple auth failures: %d in %s", state.AuthFailures, s.config.AuthFailureWindow)
			severity = "high"
		}
	} else if s.checkRateLimit(sourceIP, state) {
		alertType = "rate_limit_exceeded"
		evidence = fmt.Sprintf("Request rate: %d requests in %s", state.RequestCount, s.config.RateLimitWindow)
		severity = "medium"
	}

	// Send alert if attack detected
	if alertType != "" && s.shouldAlert(sourceIP, state) {
		alert := Alert{
			Timestamp:  time.Now().UTC().Format(time.RFC3339),
			AttackType: alertType,
			SourceIP:   sourceIP,
			Evidence:   evidence,
			Severity:   severity,
			PodName:    podName,
			DecoyURLs: []string{
				"http://decoy-frontend-1:8080",
				"http://decoy-frontend-2:8080",
				"http://decoy-frontend-3:8080",
			},
		}

		if err := s.sendAlert(alert); err != nil {
			log.Printf("[ERROR] Failed to send alert: %v", err)
		} else {
			s.mu.Lock()
			state.LastAlertTime = time.Now()
			state.AlertsSent++
			s.mu.Unlock()
		}
	}
}

func (s *Sentinel) streamPodLogs(ctx context.Context, podName string) {
	logOptions := &corev1.PodLogOptions{
		Follow:    true,
		TailLines: int64Ptr(10),
	}

	req := s.clientset.CoreV1().Pods(s.config.Namespace).GetLogs(podName, logOptions)
	stream, err := req.Stream(ctx)
	if err != nil {
		log.Printf("[ERROR] Failed to stream logs for pod %s: %v", podName, err)
		return
	}
	defer stream.Close()

	log.Printf("[INFO] Streaming logs from pod: %s", podName)

	buf := make([]byte, 2000)
	for {
		select {
		case <-ctx.Done():
			return
		default:
			n, err := stream.Read(buf)
			if err != nil {
				log.Printf("[WARN] Log stream ended for pod %s: %v", podName, err)
				return
			}

			if n > 0 {
				lines := strings.Split(string(buf[:n]), "\n")
				for _, line := range lines {
					line = strings.TrimSpace(line)
					if line != "" {
						s.processLogLine(line, podName)
					}
				}
			}
		}
	}
}

func (s *Sentinel) watchPods(ctx context.Context) {
	factory := informers.NewSharedInformerFactoryWithOptions(
		s.clientset,
		time.Minute,
		informers.WithNamespace(s.config.Namespace),
	)

	podInformer := factory.Core().V1().Pods().Informer()

	podInformer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: func(obj interface{}) {
			pod := obj.(*corev1.Pod)
			if s.shouldWatchPod(pod) {
				log.Printf("[INFO] New pod detected: %s", pod.Name)
				go s.streamPodLogs(ctx, pod.Name)
			}
		},
		UpdateFunc: func(oldObj, newObj interface{}) {
			pod := newObj.(*corev1.Pod)
			if s.shouldWatchPod(pod) && pod.Status.Phase == corev1.PodRunning {
				// Pod became running, start watching
				log.Printf("[INFO] Pod running: %s", pod.Name)
			}
		},
	})

	factory.Start(ctx.Done())
	factory.WaitForCacheSync(ctx.Done())

	// Start streaming logs for existing pods
	pods, err := s.clientset.CoreV1().Pods(s.config.Namespace).List(ctx, metav1.ListOptions{
		LabelSelector: s.config.WatchLabels,
	})
	if err != nil {
		log.Printf("[ERROR] Failed to list pods: %v", err)
		return
	}

	for _, pod := range pods.Items {
		if pod.Status.Phase == corev1.PodRunning {
			go s.streamPodLogs(ctx, pod.Name)
		}
	}

	<-ctx.Done()
}

func (s *Sentinel) shouldWatchPod(pod *corev1.Pod) bool {
	// Parse watch labels (simple key=value format)
	labels := strings.Split(s.config.WatchLabels, ",")
	for _, label := range labels {
		parts := strings.Split(label, "=")
		if len(parts) == 2 {
			key := strings.TrimSpace(parts[0])
			value := strings.TrimSpace(parts[1])
			if pod.Labels[key] != value {
				return false
			}
		}
	}
	return true
}

func int64Ptr(i int64) *int64 {
	return &i
}

func main() {
	log.Println("[SENTINEL] Starting Sentinel service...")

	config := loadConfig()
	log.Printf("[CONFIG] Controller URL: %s", config.ControllerURL)
	log.Printf("[CONFIG] Namespace: %s", config.Namespace)
	log.Printf("[CONFIG] Watch Labels: %s", config.WatchLabels)
	log.Printf("[CONFIG] Rate Limit: %d req/%s", config.RateLimitThreshold, config.RateLimitWindow)
	log.Printf("[CONFIG] Auth Failure Limit: %d failures/%s", config.AuthFailureLimit, config.AuthFailureWindow)
	log.Printf("[CONFIG] Cooldown Period: %s", config.CooldownPeriod)

	// Create in-cluster Kubernetes client
	k8sConfig, err := rest.InClusterConfig()
	if err != nil {
		log.Fatalf("[FATAL] Failed to create in-cluster config: %v", err)
	}

	clientset, err := kubernetes.NewForConfig(k8sConfig)
	if err != nil {
		log.Fatalf("[FATAL] Failed to create Kubernetes client: %v", err)
	}

	sentinel, err := NewSentinel(config, clientset)
	if err != nil {
		log.Fatalf("[FATAL] Failed to create sentinel: %v", err)
	}

	ctx := context.Background()

	log.Println("[SENTINEL] Starting pod watcher...")
	sentinel.watchPods(ctx)
}
