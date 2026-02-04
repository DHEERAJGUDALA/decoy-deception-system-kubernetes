package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

type Config struct {
	Port           string
	IsDecoy        bool
	DecoyType      string
	DecoyLatency   int
	DecoyLogging   string
	PaymentURL     string
	ReporterURL    string
}

type Product struct {
	ID    int     `json:"id"`
	Name  string  `json:"name"`
	Price float64 `json:"price"`
}

type CartItem struct {
	ProductID int `json:"product_id"`
	Quantity  int `json:"quantity"`
}

type LoginRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type CheckoutRequest struct {
	CartItems []CartItem `json:"cart_items"`
	Total     float64    `json:"total"`
}

type MetricPayload struct {
	Timestamp  string `json:"timestamp"`
	Service    string `json:"service"`
	Method     string `json:"method"`
	Path       string `json:"path"`
	SourceIP   string `json:"source_ip"`
	StatusCode int    `json:"status_code"`
	Latency    int64  `json:"latency_ms"`
}

var config Config
var products = []Product{
	{ID: 1, Name: "Laptop", Price: 999.99},
	{ID: 2, Name: "Mouse", Price: 29.99},
	{ID: 3, Name: "Keyboard", Price: 79.99},
	{ID: 4, Name: "Monitor", Price: 299.99},
}

func loadConfig() Config {
	isDecoy := os.Getenv("IS_DECOY") == "true"
	decoyType := os.Getenv("DECOY_TYPE")
	if decoyType == "" {
		decoyType = "exact"
	}

	latency, _ := strconv.Atoi(os.Getenv("DECOY_LATENCY"))

	logging := os.Getenv("DECOY_LOGGING")
	if logging == "" {
		logging = "normal"
	}

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	paymentURL := os.Getenv("PAYMENT_SERVICE_URL")
	if paymentURL == "" {
		paymentURL = "http://localhost:8081/api/charge"
	}

	reporterURL := os.Getenv("REPORTER_URL")
	if reporterURL == "" {
		reporterURL = "http://reporter-service/api/ingest"
	}

	return Config{
		Port:         port,
		IsDecoy:      isDecoy,
		DecoyType:    decoyType,
		DecoyLatency: latency,
		DecoyLogging: logging,
		PaymentURL:   paymentURL,
		ReporterURL:  reporterURL,
	}
}

func logRequest(method, path, sourceIP string) {
	logData := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
		"service":   "frontend-api",
		"method":    method,
		"path":      path,
		"source_ip": sourceIP,
		"is_decoy":  config.IsDecoy,
	}

	logJSON, _ := json.Marshal(logData)
	log.Println(string(logJSON))

	if config.DecoyLogging == "verbose" {
		log.Printf("[VERBOSE] Request Details - Method: %s, Path: %s, IP: %s", method, path, sourceIP)
	}
}

func sendMetrics(method, path, sourceIP string, statusCode int, latency int64) {
	metric := MetricPayload{
		Timestamp:  time.Now().UTC().Format(time.RFC3339),
		Service:    "frontend-api",
		Method:     method,
		Path:       path,
		SourceIP:   sourceIP,
		StatusCode: statusCode,
		Latency:    latency,
	}

	go func() {
		defer func() {
			if r := recover(); r != nil {
				log.Printf("[WARN] Metrics send panic recovered: %v", r)
			}
		}()

		jsonData, err := json.Marshal(metric)
		if err != nil {
			return
		}

		client := &http.Client{Timeout: 2 * time.Second}
		req, err := http.NewRequest("POST", config.ReporterURL, bytes.NewBuffer(jsonData))
		if err != nil {
			return
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := client.Do(req)
		if err != nil {
			return
		}
		defer resp.Body.Close()
	}()
}

func applyDecoyBehavior() {
	if !config.IsDecoy {
		return
	}

	if config.DecoyType == "slow" && config.DecoyLatency > 0 {
		time.Sleep(time.Duration(config.DecoyLatency) * time.Millisecond)
	}
}

func loggingMiddleware(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		sourceIP := r.RemoteAddr
		if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
			sourceIP = xff
		}

		logRequest(r.Method, r.URL.Path, sourceIP)

		applyDecoyBehavior()

		next(w, r)

		latency := time.Since(start).Milliseconds()
		sendMetrics(r.Method, r.URL.Path, sourceIP, 200, latency)
	}
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":   "healthy",
		"service":  "frontend-api",
		"is_decoy": config.IsDecoy,
	})
}

func productsHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"products": products,
	})
}

func cartHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"message": "Cart endpoint - add/view items",
		"items":   []interface{}{},
	})
}

func loginHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req LoginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	if config.DecoyLogging == "verbose" {
		log.Printf("[VERBOSE] Login attempt - Username: %s", req.Username)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success": true,
		"token":   "mock-jwt-token-12345",
		"user":    req.Username,
	})
}

func checkoutHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req CheckoutRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	if config.DecoyLogging == "verbose" {
		log.Printf("[VERBOSE] Checkout - Items: %d, Total: %.2f", len(req.CartItems), req.Total)
	}

	// Call payment service
	paymentReq := map[string]interface{}{"amount": req.Total}
	paymentJSON, _ := json.Marshal(paymentReq)

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Post(config.PaymentURL, "application/json", bytes.NewBuffer(paymentJSON))
	if err != nil {
		log.Printf("[ERROR] Payment service call failed: %v", err)
		http.Error(w, "Payment service unavailable", http.StatusServiceUnavailable)
		return
	}
	defer resp.Body.Close()

	var paymentResp map[string]interface{}
	json.NewDecoder(resp.Body).Decode(&paymentResp)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"success":        true,
		"order_id":       "ORD-" + strconv.FormatInt(time.Now().Unix(), 10),
		"payment_status": paymentResp,
	})
}

func indexHandler(w http.ResponseWriter, r *http.Request) {
	mode := "NORMAL MODE"
	if config.IsDecoy {
		mode = "DECOY MODE"
	}

	html := fmt.Sprintf(`<!DOCTYPE html>
<html>
<head>
    <title>Decoy Shop</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
        h1 { color: #333; }
        .endpoint { background: #f4f4f4; padding: 10px; margin: 10px 0; border-left: 4px solid #007bff; }
        .method { color: #007bff; font-weight: bold; }
    </style>
</head>
<body>
    <h1>Decoy Deception System - Frontend API</h1>
    <p>Status: <strong>%s</strong></p>

    <h2>Available Endpoints</h2>
    <div class="endpoint"><span class="method">GET</span> /health - Health check</div>
    <div class="endpoint"><span class="method">GET</span> /api/products - List products</div>
    <div class="endpoint"><span class="method">GET</span> /api/cart - View cart</div>
    <div class="endpoint"><span class="method">POST</span> /api/login - User login</div>
    <div class="endpoint"><span class="method">POST</span> /api/checkout - Process checkout</div>

    <h2>Configuration</h2>
    <pre>
Decoy Type: %s
Latency: %dms
Logging: %s
    </pre>
</body>
</html>`, mode, config.DecoyType, config.DecoyLatency, config.DecoyLogging)

	w.Header().Set("Content-Type", "text/html")
	fmt.Fprint(w, html)
}

func main() {
	config = loadConfig()

	log.Printf("Starting frontend-api on port %s", config.Port)
	log.Printf("Decoy mode: %v, Type: %s, Latency: %dms, Logging: %s",
		config.IsDecoy, config.DecoyType, config.DecoyLatency, config.DecoyLogging)

	http.HandleFunc("/", loggingMiddleware(indexHandler))
	http.HandleFunc("/health", loggingMiddleware(healthHandler))
	http.HandleFunc("/api/products", loggingMiddleware(productsHandler))
	http.HandleFunc("/api/cart", loggingMiddleware(cartHandler))
	http.HandleFunc("/api/login", loggingMiddleware(loginHandler))
	http.HandleFunc("/api/checkout", loggingMiddleware(checkoutHandler))

	log.Fatal(http.ListenAndServe(":"+config.Port, nil))
}
