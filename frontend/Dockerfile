# Multi-stage build for React frontend with NGINX deployment
FROM node:lts-slim AS build

# Install pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# Set working directory
WORKDIR /app

# Copy package files for dependency caching
COPY package.json pnpm-lock.yaml ./

# Install dependencies
RUN pnpm install --frozen-lockfile

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
