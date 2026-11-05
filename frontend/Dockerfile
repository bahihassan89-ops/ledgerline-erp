FROM node:26-alpine AS build
WORKDIR /app
# Baked into the JS bundle at build time (Vite only reads env vars at build, not at container
# start). Empty by default -- Sentry.init() is skipped client-side when this is unset.
ARG VITE_SENTRY_DSN=""
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
