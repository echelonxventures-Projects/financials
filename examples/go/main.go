// Run a DCF from Go (standard library only).
//
//	go run ./examples/go                                  # via the CLI (stdin/stdout JSON)
//	FIN_API_URL=http://127.0.0.1:8000 go run ./examples/go # via the REST API
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"sort"
)

type statement map[string]map[string]float64 // period -> line -> value

func main() {
	config := map[string]any{
		"name":       "Acme (from Go)",
		"model_type": "dcf",
		"periods":    map[string]any{"start_year": 2025, "years": 5, "type": "annual"},
		"assumptions": map[string]any{"drivers": map[string]any{
			"revenue":        map[string]any{"value": 10_000_000},
			"revenue_growth": map[string]any{"value": []float64{0.3, 0.25, 0.2, 0.15, 0.1}},
		}},
	}

	var revenue statement
	var dcf map[string]any // mostly numbers; the API also includes per-year lists
	if url := os.Getenv("FIN_API_URL"); url != "" {
		var body struct {
			ModelResult struct {
				IncomeStatement statement `json:"income_statement"`
			} `json:"model_result"`
			DCF map[string]any `json:"dcf"`
		}
		post(url+"/dcf", map[string]any{"config": config}, &body)
		revenue, dcf = body.ModelResult.IncomeStatement, body.DCF
	} else {
		var result struct {
			IncomeStatement statement                  `json:"income_statement"`
			Analysis        map[string]json.RawMessage `json:"analysis"`
		}
		runCLI(config, &result)
		revenue = result.IncomeStatement
		if err := json.Unmarshal(result.Analysis["DCF Valuation"], &dcf); err != nil {
			log.Fatal(err)
		}
	}

	periods := make([]string, 0, len(revenue))
	for p := range revenue {
		periods = append(periods, p)
	}
	sort.Strings(periods)
	for _, p := range periods {
		fmt.Printf("%s  revenue %.0f\n", p, revenue[p]["Revenue"])
	}
	fmt.Printf("Enterprise value (Gordon): %.0f\n", dcf["ev_gordon"].(float64))
	fmt.Printf("Equity value (Gordon):     %.0f\n", dcf["equity_gordon"].(float64))
}

// runCLI pipes the config to `financial-model run - -o -` and decodes the JSON result.
func runCLI(config any, out any) {
	exe := os.Getenv("FINANCIAL_MODEL")
	if exe == "" {
		exe = "financial-model"
	}
	in, _ := json.Marshal(config)
	cmd := exec.Command(exe, "run", "-", "-o", "-")
	cmd.Stdin, cmd.Stderr = bytes.NewReader(in), os.Stderr // errors: one line on stderr, non-zero exit
	stdout, err := cmd.Output()
	if err != nil {
		log.Fatal(err)
	}
	if err := json.Unmarshal(stdout, out); err != nil {
		log.Fatal(err)
	}
}

func post(url string, payload any, out any) {
	body, _ := json.Marshal(payload)
	req, _ := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if key := os.Getenv("FIN_API_KEY"); key != "" {
		req.Header.Set("X-API-Key", key)
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Fatal(err)
	}
	defer res.Body.Close()
	data, _ := io.ReadAll(res.Body)
	if res.StatusCode != http.StatusOK {
		log.Fatalf("%d: %s", res.StatusCode, data)
	}
	if err := json.Unmarshal(data, out); err != nil {
		log.Fatal(err)
	}
}
