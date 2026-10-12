# RPM in Grafana through Alloy

The IT8613E driver exposes standard Linux hwmon fan inputs. A Prometheus Node
Exporter with its hwmon collector can emit:

```prometheus
node_hwmon_fan_rpm{chip="platform_it87_2608",sensor="fan2"} 4000
node_hwmon_fan_rpm{chip="platform_it87_2608",sensor="fan3"} 3000
```

These are example readings. `fan2` is the CPU fan and `fan3` is the case fan
on the supported EQ12. Check the actual chip label on the machine being
monitored.

1. Verify that Node Exporter's `/metrics` includes `node_hwmon_fan_rpm`.
2. Use the existing Alloy Node Exporter scrape when possible, and check that
   metric relabeling permits this metric. When Alloy is centrally managed,
   make the change in the relevant Fleet Management configuration.
3. Verify the series in the Prometheus-compatible Grafana data source and
   add panels with RPM as the unit.

For a containerized Alloy, the host exporter's address is typically
`<unraid-host>:9100`. Container `localhost` refers to the container's own
network namespace. A built-in Unix exporter needs access to the host's sysfs.

Example queries:

```promql
node_hwmon_fan_rpm{chip="platform_it87_2608",sensor="fan2"}
node_hwmon_fan_rpm{chip="platform_it87_2608",sensor="fan3"}
```

Add an `instance` selector when monitoring multiple hosts. No separate plugin
exporter is required for the hardware RPM values. PWM targets or protection
state would need additional application metrics, which are not implemented.
