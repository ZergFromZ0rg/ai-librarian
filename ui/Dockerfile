FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build

# The unprivileged image runs entirely as a non-root user and listens on 8080.
# nginx.conf is a template: the entrypoint runs envsubst for ${APP_TOKEN_HEADER}
# (only — see NGINX_ENVSUBST_FILTER in docker-compose.yml) and writes conf.d.
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
