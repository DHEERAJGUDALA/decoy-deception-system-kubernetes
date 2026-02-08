package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"sync"
	"time"
)

type Config struct {
	Port            string
	HistoryDuration time.Duration
	CleanupInterval time.Duration
}

type Metric struct {
	Timestamp  string                 `json:"timestamp"`
	Service    string                 `json:"service"`
	Method     string                 `json:"method,omitempty"`
	Path       string                 `json:"path,omitempty"`
	SourceIP   string                 `json:"source_ip,omitempty"`
	StatusCode int                    `json:"status_code,omitempty"`
	Latency    int64                  `json:"latency_ms,omitempty"`
	Custom     map[string]interface{} `json:"custom,omitempty"`
}

type AggregatedStats struct {
	TotalRequests     int            `json:"total_requests"`
	RequestsByService map[string]int `json:"requests_by_service"`
	RequestsByIP      map[string]int `json:"requests_by_ip"`
	RequestsByPath    map[string]int `json:"requests_by_path"`
	AverageLatency    float64        `json:"average_latency_ms"`
	StatusCodeCounts  map[int]int    `json:"status_code_counts"`
	UniqueIPs         int            `json:"unique_ips"`
	TimeRange         string         `json:"time_range"`
	LastUpdated       string         `json:"last_updated"`
}

type Reporter struct {
	config  Config
	metrics []Metric
	mu      sync.RWMutex
}

func NewReporter(config Config) *Reporter {
	return &Reporter{
		config:  config,
		metrics: make([]Metric, 0, 1000),
	}
}

func (r *Reporter) ingestMetric(metric Metric) {
	r.mu.Lock()
	defer r.mu.Unlock()

	// Add timestamp if not provided
	if metric.Timestamp == "" {
		metric.Timestamp = time.Now().UTC().Format(time.RFC3339)
	}

	r.metrics = append(r.metrics, metric)

	// Log metric
	log.Printf("[INGEST] %s from %s - %s %s (status: %d, latency: %dms)",
		metric.Service, metric.SourceIP, metric.Method, metric.Path,
		metric.StatusCode, metric.Latency)
}

func (r *Reporter) cleanupOldMetrics() {
	r.mu.Lock()
	defer r.mu.Unlock()

	cutoff := time.Now().Add(-r.config.HistoryDuration)
	newMetrics := make([]Metric, 0, len(r.metrics))

	for _, m := range r.metrics {
		ts, err := time.Parse(time.RFC3339, m.Timestamp)
		if err != nil || ts.After(cutoff) {
			newMetrics = append(newMetrics, m)
		}
	}

	removed := len(r.metrics) - len(newMetrics)
	r.metrics = newMetrics

	if removed > 0 {
		log.Printf("[CLEANUP] Removed %d old metrics, retained %d", removed, len(r.metrics))
	}
}

func (r *Reporter) getAggregatedStats() AggregatedStats {
	r.mu.RLock()
	defer r.mu.RUnlock()

	stats := AggregatedStats{
		RequestsByService: make(map[string]int),
		RequestsByIP:      make(map[string]int),
		RequestsByPath:    make(map[string]int),
		StatusCodeCounts:  make(map[int]int),
		LastUpdated:       time.Now().UTC().Format(time.RFC3339),
	}

	if len(r.metrics) == 0 {
		stats.TimeRange = "No data"
		return stats
	}

	var totalLatency int64
	var latencyCount int64
	uniqueIPs := make(map[string]bool)

	// Find time range
	var oldest, newest time.Time
	for i, m := range r.metrics {
		ts, err := time.Parse(time.RFC3339, m.Timestamp)
		if err != nil {
			continue
		}

		if i == 0 {
			oldest = ts
			newest = ts
		} else {
			if ts.Before(oldest) {
				oldest = ts
			}
			if ts.After(newest) {
				newest = ts
			}
		}

		// Aggregate stats
		stats.TotalRequests++

		if m.Service != "" {
			stats.RequestsByService[m.Service]++
		}

		if m.SourceIP != "" {
			stats.RequestsByIP[m.SourceIP]++
			uniqueIPs[m.SourceIP] = true
		}

		if m.Path != "" {
			stats.RequestsByPath[m.Path]++
		}

		if m.StatusCode > 0 {
			stats.StatusCodeCounts[m.StatusCode]++
		}

		if m.Latency > 0 {
			totalLatency += m.Latency
			latencyCount++
		}
	}

	stats.UniqueIPs = len(uniqueIPs)

	if latencyCount > 0 {
		stats.AverageLatency = float64(totalLatency) / float64(latencyCount)
	}

	duration := newest.Sub(oldest)
	stats.TimeRange = oldest.Format(time.RFC3339) + " to " + newest.Format(time.RFC3339) +
		" (" + duration.Round(time.Second).String() + ")"

	return stats
}

func (r *Reporter) getServiceBreakdown() map[string]interface{} {
	r.mu.RLock()
	defer r.mu.RUnlock()

	breakdown := make(map[string]map[string]interface{})

	for _, m := range r.metrics {
		if m.Service == "" {
			continue
		}

		if _, exists := breakdown[m.Service]; !exists {
			breakdown[m.Service] = map[string]interface{}{
				"total_requests": 0,
				"unique_ips":     make(map[string]bool),
				"paths":          make(map[string]int),
				"avg_latency":    float64(0),
				"total_latency":  int64(0),
				"latency_count":  int64(0),
			}
		}

		svc := breakdown[m.Service]
		svc["total_requests"] = svc["total_requests"].(int) + 1

		if m.SourceIP != "" {
			svc["unique_ips"].(map[string]bool)[m.SourceIP] = true
		}

		if m.Path != "" {
			paths := svc["paths"].(map[string]int)
			paths[m.Path]++
		}

		if m.Latency > 0 {
			svc["total_latency"] = svc["total_latency"].(int64) + m.Latency
			svc["latency_count"] = svc["latency_count"].(int64) + 1
		}
	}

	// Calculate averages and convert unique IPs to count
	result := make(map[string]interface{})
	for service, data := range breakdown {
		svcData := make(map[string]interface{})
		svcData["total_requests"] = data["total_requests"]
		svcData["unique_ips"] = len(data["unique_ips"].(map[string]bool))
		svcData["paths"] = data["paths"]

		totalLatency := data["total_latency"].(int64)
		latencyCount := data["latency_count"].(int64)
		if latencyCount > 0 {
			svcData["avg_latency"] = float64(totalLatency) / float64(latencyCount)
		} else {
			svcData["avg_latency"] = 0.0
		}

		result[service] = svcData
	}

	return result
}

func (r *Reporter) handleIngest(w http.ResponseWriter, req *http.Request) {
	if req.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var metric Metric
	if err := json.NewDecoder(req.Body).Decode(&metric); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}

	r.ingestMetric(metric)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success": true,
		"message": "Metric ingested",
	})
}

func (r *Reporter) handleStats(w http.ResponseWriter, req *http.Request) {
	stats := r.getAggregatedStats()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(stats)
}

func (r *Reporter) handleServiceBreakdown(w http.ResponseWriter, req *http.Request) {
	breakdown := r.getServiceBreakdown()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(breakdown)
}

func (r *Reporter) handleHealth(w http.ResponseWriter, req *http.Request) {
	r.mu.RLock()
	metricCount := len(r.metrics)
	r.mu.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":           "healthy",
		"service":          "reporter",
		"metric_count":     metricCount,
		"history_duration": r.config.HistoryDuration.String(),
	})
}

func (r *Reporter) startCleanupWorker() {
	ticker := time.NewTicker(r.config.CleanupInterval)
	defer ticker.Stop()

	for range ticker.C {
		r.cleanupOldMetrics()
	}
}

func loadConfig() Config {
	port := "8080"
	if p := os.Getenv("PORT"); p != "" {
		port = p
	}

	historyDuration := 30 * time.Minute
	if h := os.Getenv("HISTORY_DURATION"); h != "" {
		if d, err := time.ParseDuration(h); err == nil {
			historyDuration = d
		}
	}

	cleanupInterval := 5 * time.Minute
	if c := os.Getenv("CLEANUP_INTERVAL"); c != "" {
		if d, err := time.ParseDuration(c); err == nil {
			cleanupInterval = d
		}
	}

	return Config{
		Port:            port,
		HistoryDuration: historyDuration,
		CleanupInterval: cleanupInterval,
	}
}

func main() {
	log.Println("[REPORTER] Starting Reporter service...")

	config := loadConfig()
	log.Printf("[CONFIG] Port: %s", config.Port)
	log.Printf("[CONFIG] History Duration: %s", config.HistoryDuration)
	log.Printf("[CONFIG] Cleanup Interval: %s", config.CleanupInterval)

	reporter := NewReporter(config)

	// Start cleanup worker
	go reporter.startCleanupWorker()

	// HTTP endpoints
	http.HandleFunc("/api/ingest", reporter.handleIngest)
	http.HandleFunc("/api/stats", reporter.handleStats)
	http.HandleFunc("/api/services", reporter.handleServiceBreakdown)
	http.HandleFunc("/health", reporter.handleHealth)

	log.Printf("[HTTP] Listening on port %s", config.Port)
	log.Fatal(http.ListenAndServe(":"+config.Port, nil))
}
