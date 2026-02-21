# syntax=docker/dockerfile:1

############################
# Stage 1: Build TA-Lib C library from source
############################
FROM python:3.13-slim AS talib-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q https://github.com/ta-lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz \
    && tar -xzf ta-lib-0.6.4-src.tar.gz \
    && cd ta-lib-0.6.4 \
    && ./configure --prefix=/usr/local \
    && make -j$(nproc) \
    && make install \
    && cd .. \
    && rm -rf ta-lib-0.6.4 ta-lib-0.6.4-src.tar.gz

############################
# Stage 2: Install Python dependencies
############################
FROM python:3.13-slim AS deps

COPY --from=talib-builder /usr/local/lib/libta_lib* /usr/local/lib/
COPY --from=talib-builder /usr/local/include/ta-lib /usr/local/include/ta-lib
RUN ldconfig

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

############################
# Stage 3: Runtime
############################
FROM python:3.13-slim AS runner
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    dumb-init \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (matching Guardian Assist pattern)
RUN groupadd --system --gid 1001 appgroup && \
    useradd --system --uid 1001 --gid appgroup botuser

# TA-Lib shared libraries
COPY --from=talib-builder /usr/local/lib/libta_lib* /usr/local/lib/
RUN ldconfig

# Python packages
COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Application code
COPY bot/ ./bot/

# Logs directory
RUN mkdir -p /app/logs && chown -R botuser:appgroup /app

USER botuser

EXPOSE 8080

# dumb-init for proper signal handling (SIGTERM from Fly.io)
CMD ["dumb-init", "python", "-m", "bot.main"]
