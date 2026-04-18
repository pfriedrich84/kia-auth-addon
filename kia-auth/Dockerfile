ARG BUILD_FROM
FROM ${BUILD_FROM}

# Avoid pip cache and apk cache bloat
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DISPLAY=:99

# System packages: browser stack + display server + noVNC + Python runtime.
# Font packages are required or Chromium renders blank/placeholder glyphs.
RUN apk add --no-cache \
    chromium \
    chromium-chromedriver \
    xvfb \
    x11vnc \
    novnc \
    python3 \
    py3-pip \
    ttf-freefont \
    font-noto \
    dbus \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

# Python dependencies
COPY kia-auth/requirements.txt /tmp/requirements.txt
RUN pip install --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Application code
COPY kia-auth/app /app

# s6-overlay services + any other rootfs overlay files
COPY kia-auth/rootfs /

# Ensure service run scripts are executable
RUN chmod +x /etc/s6-overlay/s6-rc.d/*/run

WORKDIR /app

# HA base images already set ENTRYPOINT ["/init"] — s6 takes over from here.
