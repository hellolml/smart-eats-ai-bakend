FROM node:20-alpine AS build

WORKDIR /frontend

COPY package*.json /frontend/
RUN npm ci

COPY . /frontend

ARG APP_API_BASE_URL
ENV APP_API_BASE_URL=${APP_API_BASE_URL}

RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /frontend/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
