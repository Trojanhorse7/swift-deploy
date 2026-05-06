# Domain: canary safety — may we promote TO canary given recent traffic SLOs?
# Thresholds MUST come from input.thresholds only.

package swiftdeploy.canary

decision := {
	"allowed": true,
	"domain": "canary",
	"phase": input.phase,
	"promotion_target": input.promotion_target,
	"reasons": ["Promotion to stable does not require canary SLO gate."],
	"details": {"skipped": true},
} {
	input.promotion_target == "stable"
}

decision := {
	"allowed": count(reasons) == 0,
	"domain": "canary",
	"phase": input.phase,
	"promotion_target": input.promotion_target,
	"reasons": sort([r | reasons[r]]),
	"details": {
		"window_seconds": input.metrics.window_seconds,
		"error_rate_percent": input.metrics.error_rate_percent,
		"p99_latency_ms": input.metrics.p99_latency_ms,
		"thresholds": input.thresholds,
	},
} {
	input.promotion_target == "canary"
}

reasons[msg] {
	input.promotion_target == "canary"
	to_number(input.metrics.error_rate_percent) > to_number(input.thresholds.max_error_rate_percent)
	msg := sprintf(
		"Policy violation: error rate (%.4f%%) exceeds maximum (%.4f%%) over last %v seconds.",
		[
			to_number(input.metrics.error_rate_percent),
			to_number(input.thresholds.max_error_rate_percent),
			input.metrics.window_seconds,
		],
	)
}

reasons[msg] {
	input.promotion_target == "canary"
	to_number(input.metrics.p99_latency_ms) > to_number(input.thresholds.max_p99_latency_ms)
	msg := sprintf(
		"Policy violation: P99 latency (%.2f ms) exceeds maximum (%.2f ms) over last %v seconds.",
		[
			to_number(input.metrics.p99_latency_ms),
			to_number(input.thresholds.max_p99_latency_ms),
			input.metrics.window_seconds,
		],
	)
}
