package reporterclient

import (
	"bytes"
	"encoding/json"
	"net/http"
	"time"
)

// Client is a lightweight reporter client
type Client struct {
	URL    string
	client *http.Client
}

// Metric represents a metric to send to reporter
type Metric struct {
	Timestamp  string                 `json:"timestamp,omitempty"`
	Service    string                 `json:"service"`
	Method     string                 `json:"method,omitempty"`
	Path       string                 `json:"path,omitempty"`
	SourceIP   string                 `json:"source_ip,omitempty"`
	StatusCode int                    `json:"status_code,omitempty"`
	Latency    int64                  `json:"latency_ms,omitempty"`
	Custom     map[string]interface{} `json:"custom,omitempty"`
}

// NewClient creates a new reporter client
func NewClient(url string) *Client {
	return &Client{
		URL: url,
		client: &http.Client{
			Timeout: 2 * time.Second,
		},
	}
}

// Send sends a metric to the reporter (fire-and-forget)
func (c *Client) Send(metric Metric) error {
	// Add timestamp if not provided
	if metric.Timestamp == "" {
		metric.Timestamp = time.Now().UTC().Format(time.RFC3339)
	}

	data, err := json.Marshal(metric)
	if err != nil {
		return err
	}

	// Fire and forget - don't wait for response
	go func() {
		resp, err := c.client.Post(c.URL+"/api/ingest", "application/json", bytes.NewBuffer(data))
		if err != nil {
			// Silently fail - metrics are best-effort
			return
		}
		defer resp.Body.Close()
	}()

	return nil
}

// SendSync sends a metric synchronously (blocks until complete)
func (c *Client) SendSync(metric Metric) error {
	if metric.Timestamp == "" {
		metric.Timestamp = time.Now().UTC().Format(time.RFC3339)
	}

	data, err := json.Marshal(metric)
	if err != nil {
		return err
	}

	resp, err := c.client.Post(c.URL+"/api/ingest", "application/json", bytes.NewBuffer(data))
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	return nil
}

// Example usage in other services:
//
// import reporterclient "github.com/decoy-deception-system/reporter/client"
//
// reporter := reporterclient.NewClient("http://reporter-service:8080")
//
// reporter.Send(reporterclient.Metric{
//     Service:    "frontend-api",
//     Method:     "GET",
//     Path:       "/api/products",
//     SourceIP:   "192.168.1.100",
//     StatusCode: 200,
//     Latency:    45,
// })
