# Validation and limitations

Hardware validation used a Beelink EQ12 with an AZW EQ board, Intel N100 and
IT8613E, running Unraid 7.2.4, kernel 6.12.54-Unraid, Python 3.9.18 and PHP
8.3.26.

## Automated tests

Python tests cover interpolation, endpoints, hysteresis, unavailable
sensors, the independent CPU limit, a stalled fan, invalid curves and sensor
IDs after hwmon renumbering. Tests of the guardian include normal restoration,
competing hardware locks, a killed guardian, a killed controller and a stalled
controller with a missing heartbeat.

Browser tests use simulated data and cover curve edits, validation, sensor
selection, save/apply, automatic mode and mobile layout. Dashboard tests cover
one/two active fans, hardware automatic mode, temperature-sensor protection,
stopped services, stale status, controller errors, waiting control and network
failure. Dashboard polling is read-only.

Timed-test coverage includes API input limits, no changes to saved curves,
temperature/sensor/stall overrides, pending-test removal on daemon startup,
expiry back to hardware automatic mode with its original shared register,
resumption of an enabled curve, updates to fixed duty without hysteresis,
unchanged operation of the other fan and bounded diagnostic trace rotation.
Daemon lifecycle tests use temporary hwmon files and a real guardian process.
Browser tests cover start/stop, dashboard test status and diagnostic download.

## Live validation

On the supported hardware, the following checks passed:

- Real Unraid authentication, sensor selection and independent fan curves.
- Full-speed operation, followed by high-duty linear interpolation using
  distinct CPU and NVMe sensors.
- Server-side rejection of invalid curves and Unraid CSRF enforcement.
- Per-fan enable/disable and restoration of each original automatic start
  value.
- Prevention of competing control through the shared lock.
- An intentional SIGKILL of the controller, followed by restoration of all
  original automatic parameters by the independent guardian.
- Dashboard RPM, temperatures, duty, control state, periodic refresh and
  navigation to settings and back.
- Display-only plugin updates preserved the running controller process and
  configuration.

For the timed-test update, the installed settings page, dashboard, diagnostic
download, invalid-duty rejection and Unraid CSRF enforcement were checked on
the EQ12. Saved settings and the system boot ID were unchanged. No fixed-PWM
hardware sweep was started during this update; the new test lifecycle and
protection behavior were verified with simulated hwmon data.

## Limits

No actual reboot, array stop or kernel upgrade was performed during this
validation. The application is installed through Unraid's persistent plugin
mechanism; the IT87 kernel module still needs a matching driver package after
a kernel change.

Live fan tests used full or high duty. The 20% configurable floor is not a
measured startup guarantee. The runtime detects unavailable RPM or less than
200 RPM after the eight-second startup period and requests full speed.

Other boards, Beelink models, wiring layouts and controller revisions have not
been validated. The current control mapping and board check are specifically
for the AZW EQ / IT8613E combination.
