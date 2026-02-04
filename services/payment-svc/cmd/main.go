package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

type Config struct {
	Port         string
	IsDecoy      bool
	DecoyType    string
	DecoyLatency int
	DecoyLogging string
}

type ChargeRequest struct {
	Amount float64 `json:"amount"`
}

type ChargeResponse struct {
	Success       bool    `json:"success"`
	TransactionID string  `json:"transaction_id"`
	Amount        float64 `json:"amount"`
	Message       string  `json:"message"`
}

var config Config

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
		port = "8081"
	}

	return Config{
		Port:         port,
		IsDecoy:      isDecoy,
		DecoyType:    decoyType,
		DecoyLatency: latency,
		DecoyLogging: logging,
	}
}

func logRequest(method, path, sourceIP string) {
	logData := map[string]interface{}{
		"timestamp": time.Now().UTC().Format(time.RFC3339),
		"service":   "payment-svc",
		"method":    method,
		"path":      path,
		"source_ip": sourceIP,
		"is_decoy":  config.IsDecoy,
	}

	logJSON, _ := json.Marshal(logData)
	log.Println(string(logJSON))

	if config.DecoyLogging == "verbose" {
		log.Printf("[VERBOSE] Payment Request - Method: %s, Path: %s, IP: %s", method, path, sourceIP)
	}
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
		sourceIP := r.RemoteAddr
		if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
			sourceIP = xff
		}

		logRequest(r.Method, r.URL.Path, sourceIP)

		applyDecoyBehavior()

		next(w, r)
	}
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":   "healthy",
		"service":  "payment-svc",
		"is_decoy": config.IsDecoy,
	})
}

func chargeHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req ChargeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	if config.DecoyLogging == "verbose" {
		log.Printf("[VERBOSE] Charge Request - Amount: %.2f", req.Amount)
	}

	// Simulate payment processing
	txID := "TXN-" + strconv.FormatInt(time.Now().UnixNano(), 36)

	response := ChargeResponse{
		Success:       true,
		TransactionID: txID,
		Amount:        req.Amount,
		Message:       "Payment processed successfully",
	}

	if config.DecoyLogging == "verbose" {
		respJSON, _ := json.Marshal(response)
		log.Printf("[VERBOSE] Charge Response: %s", string(respJSON))
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func main() {
	config = loadConfig()

	log.Printf("Starting payment-svc on port %s", config.Port)
	log.Printf("Decoy mode: %v, Type: %s, Latency: %dms, Logging: %s",
		config.IsDecoy, config.DecoyType, config.DecoyLatency, config.DecoyLogging)

	http.HandleFunc("/health", loggingMiddleware(healthHandler))
	http.HandleFunc("/api/charge", loggingMiddleware(chargeHandler))

	log.Fatal(http.ListenAndServe(":"+config.Port, nil))
}
