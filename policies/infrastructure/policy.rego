# Domain: infrastructure — may we deploy given host resource signals?
# Thresholds MUST come from input.thresholds only (no numeric literals for limits).

package swiftdeploy.infrastructure

# Single exported decision document (never a bare boolean).
decision := {
	"allowed": count(reasons) == 0,
	"domain": "infrastructure",
	"phase": input.phase,
	"reasons": sort([r | reasons[r]]),
	"details": {
		"disk_free_gb": input.host.disk_free_gb,
		"cpu_load_1m": input.host.cpu_load_1m,
		"thresholds": input.thresholds,
	},
}

reasons[msg] {
	input.phase == "pre_deploy"
	to_number(input.host.disk_free_gb) < to_number(input.thresholds.min_disk_free_gb)
	msg := sprintf(
		"Policy violation: disk free (%.2f GB) is below minimum required (%.2f GB).",
		[to_number(input.host.disk_free_gb), to_number(input.thresholds.min_disk_free_gb)],
	)
}

reasons[msg] {
	input.phase == "pre_deploy"
	to_number(input.host.cpu_load_1m) > to_number(input.thresholds.max_cpu_load)
	msg := sprintf(
		"Policy violation: CPU load (%.2f) exceeds maximum allowed (%.2f).",
		[to_number(input.host.cpu_load_1m), to_number(input.thresholds.max_cpu_load)],
	)
}
