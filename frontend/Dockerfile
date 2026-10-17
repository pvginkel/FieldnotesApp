# Multi-stage build for React frontend with NGINX deployment.
# The build image (DockerImages, modern-app-frontend-build) carries Node, curl
# and corepack; corepack runs the pnpm that package.json's packageManager names.
FROM registry:5000/modern-app-frontend-build AS build

# The Jenkins build cache; pass --build-arg BUILD_CACHE_URL=... if it moves.
ARG BUILD_CACHE_URL

# Set working directory
WORKDIR /app

# Copy package files for dependency caching
COPY package.json pnpm-lock.yaml ./

# Restore the pnpm store from the build cache, keyed on the lockfile (the CI
# suite run uploads it after a miss; a miss only costs time), then install.
RUN STORE="$(pnpm store path)" && mkdir -p "$STORE" && \
    { curl -sSf "${BUILD_CACHE_URL:-http://build-cache.jenkins-prd.svc.cluster.local}/pnpm-$(sha256sum pnpm-lock.yaml | cut -d' ' -f1).tgz" \
        | tar -xzf - -C "$STORE" || echo "No pnpm store in the build cache"; } && \
    pnpm install --frozen-lockfile

# Copy source code, configuration files, and cached OpenAPI schema
COPY . .

# Build the application (uses cached schema)
RUN pnpm build

# Production stage with NGINX
FROM nginx:alpine AS production

# Copy custom NGINX configuration and proxy snippet
COPY nginx.conf /etc/nginx/nginx.conf
RUN mkdir -p /etc/nginx/snippets
COPY snippets/proxy.conf /etc/nginx/snippets/proxy.conf

# Copy built static assets from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Expose port 3400
EXPOSE 3400

# Start NGINX
CMD ["nginx", "-g", "daemon off;"]
