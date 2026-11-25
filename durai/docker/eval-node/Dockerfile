FROM node:24.22.2-bookworm-slim
WORKDIR /opt/durai
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
RUN mkdir -p /opt/durai/app
WORKDIR /opt/durai/app
