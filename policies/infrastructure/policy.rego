# Domain: infrastructure - may we deploy given host resource signals?
# Thresholds MUST come from input.thresholds only (no numeric literals for limits).

package swiftdeploy.infrastructure

disk_ok := to_number(input.host.disk_free_gb) >= to_number(input.thresholds.min_disk_free_gb)

cpu_ok := to_number(input.host.cpu_load_1m) <= to_number(input.thresholds.max_cpu_load)

mem_ok := to_number(input.host.mem_available_gb) >= to_number(input.thresholds.min_mem_available_gb)

disk_detail := sprintf(
	"PASS: disk free %.2f GB meets minimum %.2f GB.",
	[to_number(input.host.disk_free_gb), to_number(input.thresholds.min_disk_free_gb)],
) {
	disk_ok
}

disk_detail := sprintf(
	"FAIL: disk free %.2f GB is below minimum %.2f GB.",
	[to_number(input.host.disk_free_gb), to_number(input.thresholds.min_disk_free_gb)],
) {
	not disk_ok
}

cpu_detail := sprintf(
	"PASS: CPU load %.2f is within maximum %.2f.",
	[to_number(input.host.cpu_load_1m), to_number(input.thresholds.max_cpu_load)],
) {
	cpu_ok
}

cpu_detail := sprintf(
	"FAIL: CPU load %.2f exceeds maximum %.2f.",
	[to_number(input.host.cpu_load_1m), to_number(input.thresholds.max_cpu_load)],
) {
	not cpu_ok
}

mem_detail := sprintf(
	"PASS: memory available %.2f GB meets minimum %.2f GB.",
	[to_number(input.host.mem_available_gb), to_number(input.thresholds.min_mem_available_gb)],
) {
	mem_ok
}

mem_detail := sprintf(
	"FAIL: memory available %.2f GB is below minimum %.2f GB.",
	[to_number(input.host.mem_available_gb), to_number(input.thresholds.min_mem_available_gb)],
) {
	not mem_ok
}

disk_check := {
	"rule_id": "infra_disk_free_minimum",
	"passed": disk_ok,
	"detail": disk_detail,
}

cpu_check := {
	"rule_id": "infra_cpu_load_maximum",
	"passed": cpu_ok,
	"detail": cpu_detail,
}

mem_check := {
	"rule_id": "infra_memory_available_minimum",
	"passed": mem_ok,
	"detail": mem_detail,
}

checks := [disk_check, cpu_check, mem_check]

decision := {
	"allowed": count(reasons) == 0,
	"domain": "infrastructure",
	"phase": input.phase,
	"reasons": sort([r | reasons[r]]),
	"checks": checks,
	"details": {
		"disk_free_gb": input.host.disk_free_gb,
		"cpu_load_1m": input.host.cpu_load_1m,
		"mem_available_gb": input.host.mem_available_gb,
		"thresholds": input.thresholds,
	},
}

reasons[msg] {
	input.phase == "pre_deploy"
	not disk_ok
	msg := sprintf(
		"Policy violation: disk free (%.2f GB) is below minimum required (%.2f GB).",
		[to_number(input.host.disk_free_gb), to_number(input.thresholds.min_disk_free_gb)],
	)
}

reasons[msg] {
	input.phase == "pre_deploy"
	not cpu_ok
	msg := sprintf(
		"Policy violation: CPU load (%.2f) exceeds maximum allowed (%.2f).",
		[to_number(input.host.cpu_load_1m), to_number(input.thresholds.max_cpu_load)],
	)
}

reasons[msg] {
	input.phase == "pre_deploy"
	not mem_ok
	msg := sprintf(
		"Policy violation: memory available (%.2f GB) is below minimum required (%.2f GB).",
		[to_number(input.host.mem_available_gb), to_number(input.thresholds.min_mem_available_gb)],
	)
}
