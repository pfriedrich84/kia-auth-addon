# Paul's KIA-Auth Helper

Custom add-ons repository for Home Assistant OS.

## Installation

1. In Home Assistant: **Settings → Add-ons → Add-on Store**
2. Click the **⋮** (three-dot menu, top right) → **Repositories**
3. Paste: `https://github.com/pfriedrich84/kia-auth-addon`
4. Click **Add** → **Close**
5. The add-ons below appear in the store

## Add-ons in this repository

### 🚗 Kia Auth

One-shot Kia Connect EU token fetcher. Solve the Google reCAPTCHA once in a
containerized browser, copy the resulting refresh token into your `kia_uvo`
integration. See [`kia-auth/DOCS.md`](./kia-auth/DOCS.md) for details.

Process:
1) Start the Plugin
<img width="945" height="444" alt="image" src="https://github.com/user-attachments/assets/05524717-38ba-4eb2-83b0-54120edb1e4f" />

2) Press Start and Wait for Open noVNC
<img width="945" height="532" alt="image" src="https://github.com/user-attachments/assets/c82c1f34-0441-46a5-a359-f1f8b561a0c2" />

3) Login on the KIA-Website

4) Token is Displayed for Login

5) Stop the Addon, Uninstall or whatever ;-)

## License

MIT
