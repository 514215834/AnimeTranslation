# 前端构建 + Nginx 静态托管与反代
FROM node:20-alpine AS build
WORKDIR /build
COPY package.json ./
RUN npm install --no-fund --no-audit
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
