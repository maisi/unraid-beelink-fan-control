#!/usr/bin/env python3
"""Build a self-contained, offline-installable Unraid .plg."""
import base64
import hashlib
import io
from pathlib import Path
import tarfile
import textwrap
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
VERSION = "2026.10.31.1"
NAME = "beelink-fan-control"
REPOSITORY = "https://github.com/maisi/unraid-beelink-fan-control"
PLUGIN_URL = "https://raw.githubusercontent.com/maisi/unraid-beelink-fan-control/main/dist/beelink-fan-control.plg"
WEB = "usr/local/emhttp/plugins/" + NAME
files = {WEB + "/" + name: ((ROOT / name).read_bytes(), 0o644)
         for name in ["control.py", "api.php", "BeelinkFans.page", "app.js", "style.css", "README.md", "LICENSE",
                      "BeelinkFans.Dashboard.page", "dashboard.js", "dashboard.css"]}
files["etc/rc.d/rc." + NAME] = ((ROOT / ("rc." + NAME)).read_bytes(), 0o755)
for event, action in [("started", "start")]:
    files[WEB + "/event/" + event] = (("#!/bin/bash\n/etc/rc.d/rc.%s %s\n" % (NAME, action)).encode(), 0o755)
archive = io.BytesIO()
with tarfile.open(fileobj=archive, mode="w:xz") as tar:
    for name, (content, mode) in sorted(files.items()):
        info = tarfile.TarInfo(name)
        info.size, info.mode, info.mtime = len(content), mode, 0
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        tar.addfile(info, io.BytesIO(content))
package = archive.getvalue()
package_path = "/boot/config/plugins/%s/%s-%s.txz" % (NAME, NAME, VERSION)
digest = hashlib.sha256(package).hexdigest()
control_digest = hashlib.sha256((ROOT / "control.py").read_bytes()).hexdigest()
encoded = "\n".join(textwrap.wrap(base64.b64encode(package).decode(), 100))
xml = '''<?xml version="1.0" standalone="yes"?>
<PLUGIN name="%s" author="maisi" version="%s" launch="Settings/BeelinkFans" min="7.2.4" pluginURL="%s" support="%s/issues">
<CHANGES>Timed per-fan fixed-PWM test mode with temperature protection, RAM-only diagnostic trace, and fewer redundant hardware writes.</CHANGES>
<FILE Name="%s" Type="base64">
<SHA256>%s</SHA256>
<INLINE>%s</INLINE>
</FILE>
<FILE Run="/bin/bash"><INLINE>
set -e
controller=/usr/local/emhttp/plugins/beelink-fan-control/control.py
if [ -x /etc/rc.d/rc.beelink-fan-control ] &amp;&amp; [ -f "$controller" ]; then
  installed_digest=$(sha256sum "$controller" | cut -d ' ' -f 1)
  if [ "$installed_digest" != "%s" ]; then /etc/rc.d/rc.beelink-fan-control stop; fi
fi
rm -f /usr/local/emhttp/plugins/beelink-fan-control/event/stopping_svcs
tar -xJf %s -C /
/usr/bin/python3 /usr/local/emhttp/plugins/beelink-fan-control/control.py init
/etc/rc.d/rc.beelink-fan-control start
</INLINE></FILE>
<FILE Run="/bin/bash" Method="remove"><INLINE>
set -e
/etc/rc.d/rc.beelink-fan-control stop
rm -f /etc/rc.d/rc.beelink-fan-control
rm -rf /usr/local/emhttp/plugins/beelink-fan-control
</INLINE></FILE>
</PLUGIN>
''' % (NAME, VERSION, PLUGIN_URL, REPOSITORY, package_path, digest, encoded, control_digest, package_path)
ET.fromstring(xml)
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
(dist / (NAME + ".plg")).write_text(xml)
(dist / (NAME + "-" + VERSION + ".txz")).write_bytes(package)
print("Built %s (%d bytes); package SHA256 %s" % (dist / (NAME + ".plg"), len(xml), digest))
