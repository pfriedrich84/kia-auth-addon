# Kia Auth — Documentation

A one-shot token fetcher for Kia Connect EU. Solve the Google reCAPTCHA once
in the add-on's internal browser, copy the resulting refresh token into your
`kia_uvo` (Kia Connect) integration.

## Why this add-on exists

Kia's EU login flow now requires a Google reCAPTCHA that cannot be automated
without violating Google's and Kia's terms of service. The community workaround
is to solve the CAPTCHA once in a real browser, capture the resulting OAuth
refresh token, and use *that* as the "password" in home automation integrations.

This add-on packages that workflow — a browser, a noVNC viewer, and a tiny web
UI — so you don't need to run Python scripts on your laptop every time the
token expires.

## Prerequisites

- Home Assistant OS (not HA Container)
- The `kia_uvo` / Kia Connect integration already installed in HA
- A Kia Connect account (same credentials you use in the Kia app)

## How to use

1. Open the add-on's Web UI (sidebar or "Open Web UI" button).
2. Click **Start authentication**.
3. When the status switches to *Awaiting login*, click **Open noVNC ↗**.
4. In the noVNC tab: solve the reCAPTCHA and log in with your Kia credentials.
5. Once you land on Kia's homepage, return to this tab. The token will appear
   automatically.
6. (Optional) Click **Validate token** to verify it is accepted by Kia's token
   endpoint.
7. Click **Copy token** and paste it into the `kia_uvo` integration's password
   field (Settings → Devices & Services → Kia Connect → Configure).
8. Stop the add-on. You won't need it until the token expires (typically weeks
   to months).

Optional: In the add-on configuration, set `novnc_resize_mode` to control noVNC
scaling (`scale` default, or `remote` / `off` for manual preference).

## Security

- **Never share the refresh token.** Anyone with it has full access to your
  Kia account and car — no email or password required.
- The token is kept in the add-on's memory only and is automatically cleared
  10 minutes after it's obtained.
- noVNC runs without a password. Do not expose port 6080 outside your LAN.
- Access to the add-on's Web UI is gated by Home Assistant's usual login.

## Troubleshooting

**"Start authentication" button hangs on *Starting...***
Chromium may have failed to launch. Check the add-on log for stack traces.
Possible causes: Alpine Chromium version incompatibility, missing fonts,
insufficient shared memory.

**noVNC shows a black screen**
Xvfb or x11vnc didn't start properly. Restart the add-on and check the log
for errors from the `xvfb` or `x11vnc` services.

**Login succeeds but no token appears**
Kia may have changed their login flow. Check the
[upstream community discussion](https://github.com/Hyundai-Kia-Connect/kia_uvo/discussions/1285)
for recent flow changes, then update the vendored `kia_flow.py` accordingly.

**"Mixed content" error when opening noVNC**
Your HA instance is served over HTTPS. Either:
- Allow mixed content for this origin in your browser settings, or
- Access HA via plain HTTP while using this add-on

## Links

- [Upstream community login flow documentation](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api/wiki/Kia-Europe-Login-Flow)
- [Upstream Python script this add-on wraps](https://gist.github.com/marvinwankersteen/af92c571881ac76579a037fac4f3a63a)
- [`kia_uvo` HA integration](https://github.com/Hyundai-Kia-Connect/kia_uvo)
