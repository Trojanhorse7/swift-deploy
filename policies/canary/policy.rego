# Domain: canary safety - may we promote TO canary given recent traffic SLOs?
# Thresholds MUST come from input.thresholds only.

package swiftdeploy.canary

decision := {
	"allowed": true,
	"domain": "canary",
	"phase": input.phase,
	"promotion_target": input.promotion_target,
	"reasons": ["Promotion to stable does not require canary SLO gate."],
	"checks": [{
		"rule_id": "canary_slo_skipped_for_stable",
		"passed": true,
		"detail": "Promotion to stable does not evaluate rolling-window SLO metrics.",
	}],
	"details": {"skipped": true},
} {
	input.promotion_target == "stable"
}

error_ok := to_number(input.metrics.error_rate_percent) <= to_number(input.thresholds.max_error_rate_percent)

p99_ok := to_number(input.metrics.p99_latency_ms) <= to_number(input.thresholds.max_p99_latency_ms)

error_detail := sprintf(
	"PASS: error rate %.4f%% within maximum %.4f%% over %v s window.",
	[
		to_number(input.metrics.error_rate_percent),
		to_number(input.thresholds.max_error_rate_percent),
		input.metrics.window_seconds,
	],
) {
	error_ok
}

error_detail := sprintf(
	"FAIL: error rate %.4f%% exceeds maximum %.4f%% over %v s window.",
	[
		to_number(input.metrics.error_rate_percent),
		to_number(input.thresholds.max_error_rate_percent),
		input.metrics.window_seconds,
	],
) {
	not error_ok
}

p99_detail := sprintf(
	"PASS: P99 latency %.2f ms within maximum %.2f ms over %v s window.",
	[
		to_number(input.metrics.p99_latency_ms),
		to_number(input.thresholds.max_p99_latency_ms),
		input.metrics.window_seconds,
	],
) {
	p99_ok
}

p99_detail := sprintf(
	"FAIL: P99 latency %.2f ms exceeds maximum %.2f ms over %v s window.",
	[
		to_number(input.metrics.p99_latency_ms),
		to_number(input.thresholds.max_p99_latency_ms),
		input.metrics.window_seconds,
	],
) {
	not p99_ok
}

error_check := {
	"rule_id": "canary_error_rate_window",
	"passed": error_ok,
	"detail": error_detail,
}

p99_check := {
	"rule_id": "canary_p99_latency_window",
	"passed": p99_ok,
	"detail": p99_detail,
}

canary_checks := [error_check, p99_check]

decision := {
	"allowed": count(reasons) == 0,
	"domain": "canary",
	"phase": input.phase,
	"promotion_target": input.promotion_target,
	"reasons": sort([r | reasons[r]]),
	"checks": canary_checks,
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
	not error_ok
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
	not p99_ok
	msg := sprintf(
		"Policy violation: P99 latency (%.2f ms) exceeds maximum (%.2f ms) over last %v seconds.",
		[
			to_number(input.metrics.p99_latency_ms),
			to_number(input.thresholds.max_p99_latency_ms),
			input.metrics.window_seconds,
		],
	)
}
