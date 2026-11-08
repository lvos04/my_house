# Test fixtures

These JSON/SSE files are **derived from the manufacturers' public API documentation**
(Hue CLIP API v2, HomeWizard API v1/v2, Zigbee2MQTT MQTT topics). They are written in
the documented response shapes with example values.

They are **not** captured from the user's own devices. Passing tests here prove that
parsing, normalization, policy hooks and protocol handling behave as specified; they do
**not** prove that a specific physical device works. Real-device status is tracked in
`docs/compatibility.md` and stays "nog niet getest met echte hardware" until verified.

When real devices are available, record responses (with serials/tokens redacted) and
add them next to these files with a `recorded-` prefix.
