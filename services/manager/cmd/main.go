package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"sync"
	"time"
)

type Config struct {
	Port                 string
	LegitimateServiceURL string
}

type BlockedIP struct {
	SourceIP  string   `json:"source_ip"`
	DecoyURLs []string `json:"decoy_urls"`
	BlockedAt time.Time
	Counter   int // Round-robin counter
}

type BlockIPRequest struct {
	SourceIP  string   `json:"source_ip"`
	DecoyURLs []string `json:"decoy_urls"`
}

type CleanupRequest struct {
	SourceIP string `json:"source_ip"`
}

type IPManager struct {
	mu         sync.RWMutex
	blockedIPs map[string]*BlockedIP
}

var (
	config     Config
	ipManager  *IPManager
	legitProxy *httputil.ReverseProxy
)

func NewIPManager() *IPManager {
	return &IPManager{
		blockedIPs: make(map[string]*BlockedIP),
	}
}

func (m *IPManager) BlockIP(sourceIP string, decoyURLs []string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if len(decoyURLs) != 3 {
		log.Printf("[WARN] Expected exactly 3 decoy URLs for %s, got %d", sourceIP, len(decoyURLs))
	}

	m.blockedIPs[sourceIP] = &BlockedIP{
		SourceIP:  sourceIP,
		DecoyURLs: decoyURLs,
		BlockedAt: time.Now(),
		Counter:   0,
	}

	logData := map[string]interface{}{
		"timestamp":  time.Now().UTC().Format(time.RFC3339),
		"action":     "block_ip",
		"source_ip":  sourceIP,
		"decoy_urls": decoyURLs,
	}
	logJSON, _ := json.Marshal(logData)
	log.Println(string(logJSON))
}

func (m *IPManager) CleanupIP(sourceIP string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()

	if _, exists := m.blockedIPs[sourceIP]; exists {
		delete(m.blockedIPs, sourceIP)

		logData := map[string]interface{}{
			"timestamp": time.Now().UTC().Format(time.RFC3339),
			"action":    "cleanup_ip",
			"source_ip": sourceIP,
		}
		logJSON, _ := json.Marshal(logData)
		log.Println(string(logJSON))
		return true
	}
	return false
}

func (m *IPManager) GetDecoyURL(sourceIP string) (string, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()

	blocked, exists := m.blockedIPs[sourceIP]
	if !exists {
		return "", false
	}

	if len(blocked.DecoyURLs) == 0 {
		return "", false
	}

	// Round-robin selection
	selectedURL := blocked.DecoyURLs[blocked.Counter%len(blocked.DecoyURLs)]
	blocked.Counter++

	logData := map[string]interface{}{
		"timestamp":         time.Now().UTC().Format(time.RFC3339),
		"action":            "route_to_decoy",
		"source_ip":         sourceIP,
		"selected_url":      selectedURL,
		"round_robin_count": blocked.Counter,
	}
	logJSON, _ := json.Marshal(logData)
	log.Println(string(logJSON))

	return selectedURL, true
}

func (m *IPManager) IsBlocked(sourceIP string) bool {
	m.mu.RLock()
	defer m.mu.RUnlock()
	_, exists := m.blockedIPs[sourceIP]
	return exists
}

func (m *IPManager) GetStats() map[string]interface{} {
	m.mu.RLock()
	defer m.mu.RUnlock()

	stats := map[string]interface{}{
		"total_blocked_ips": len(m.blockedIPs),
		"blocked_ips":       []string{},
	}

	blockedList := []string{}
	for ip := range m.blockedIPs {
		blockedList = append(blockedList, ip)
	}
	stats["blocked_ips"] = blockedList

	return stats
}

func loadConfig() Config {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	legitURL := os.Getenv("LEGITIMATE_SERVICE_URL")
	if legitURL == "" {
		legitURL = "http://frontend-api:8080"
	}

	return Config{
		Port:                 port,
		LegitimateServiceURL: legitURL,
	}
}

func extractSourceIP(r *http.Request) string {
	// Check X-Forwarded-For first
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		return xff
	}
	// Check X-Real-IP
	if xri := r.Header.Get("X-Real-IP"); xri != "" {
		return xri
	}
	// Fallback to RemoteAddr
	return r.RemoteAddr
}

func reverseProxyHandler(w http.ResponseWriter, r *http.Request) {
	sourceIP := extractSourceIP(r)

	// Check if IP is blocked
	if decoyURL, isBlocked := ipManager.GetDecoyURL(sourceIP); isBlocked {
		// Route to decoy (round-robin)
		proxyToDecoy(w, r, decoyURL, sourceIP)
		return
	}

	// Route to legitimate service
	logData := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
		"action":    "route_to_legitimate",
		"source_ip": sourceIP,
		"method":    r.Method,
		"path":      r.URL.Path,
	}
	logJSON, _ := json.Marshal(logData)
	log.Println(string(logJSON))

	legitProxy.ServeHTTP(w, r)
}

func proxyToDecoy(w http.ResponseWriter, r *http.Request, decoyURL string, sourceIP string) {
	targetURL, err := url.Parse(decoyURL)
	if err != nil {
		log.Printf("[ERROR] Invalid decoy URL: %s, error: %v", decoyURL, err)
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}

	// Create a reverse proxy for this decoy
	proxy := httputil.NewSingleHostReverseProxy(targetURL)

	// Customize the request
	originalDirector := proxy.Director
	proxy.Director = func(req *http.Request) {
		originalDirector(req)
		req.Host = targetURL.Host
		req.Header.Set("X-Forwarded-For", sourceIP)
		req.Header.Set("X-Decoy-Routed", "true")
	}

	proxy.ServeHTTP(w, r)
}

func blockIPHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req BlockIPRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	if req.SourceIP == "" {
		http.Error(w, "source_ip is required", http.StatusBadRequest)
		return
	}

	if len(req.DecoyURLs) == 0 {
		http.Error(w, "decoy_urls array is required", http.StatusBadRequest)
		return
	}

	ipManager.BlockIP(req.SourceIP, req.DecoyURLs)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success":    true,
		"message":    fmt.Sprintf("IP %s blocked and routed to %d decoy URLs", req.SourceIP, len(req.DecoyURLs)),
		"source_ip":  req.SourceIP,
		"decoy_urls": req.DecoyURLs,
	})
}

func cleanupHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req CleanupRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	if req.SourceIP == "" {
		http.Error(w, "source_ip is required", http.StatusBadRequest)
		return
	}

	removed := ipManager.CleanupIP(req.SourceIP)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success":   removed,
		"message":   fmt.Sprintf("IP %s cleanup result", req.SourceIP),
		"source_ip": req.SourceIP,
		"removed":   removed,
	})
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":  "healthy",
		"service": "manager",
		"stats":   ipManager.GetStats(),
	})
}

func statsHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(ipManager.GetStats())
}

func main() {
	config = loadConfig()
	ipManager = NewIPManager()

	// Setup legitimate service reverse proxy
	legitURL, err := url.Parse(config.LegitimateServiceURL)
	if err != nil {
		log.Fatalf("Invalid legitimate service URL: %v", err)
	}

	legitProxy = httputil.NewSingleHostReverseProxy(legitURL)
	legitProxy.Director = func(req *http.Request) {
		req.URL.Scheme = legitURL.Scheme
		req.URL.Host = legitURL.Host
		req.Host = legitURL.Host
	}

	log.Printf("Starting manager service on port %s", config.Port)
	log.Printf("Legitimate service URL: %s", config.LegitimateServiceURL)

	// Management endpoints
	http.HandleFunc("/api/block_ip", blockIPHandler)
	http.HandleFunc("/api/cleanup", cleanupHandler)
	http.HandleFunc("/health", healthHandler)
	http.HandleFunc("/api/stats", statsHandler)

	// Reverse proxy for all other requests
	http.HandleFunc("/", reverseProxyHandler)

	log.Fatal(http.ListenAndServe(":"+config.Port, nil))
}
