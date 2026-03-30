FROM node:20-alpine AS build
WORKDIR /app

ARG VITE_API_BASE
ENV VITE_API_BASE=${VITE_API_BASE}
ARG VITE_TIME_ZONE
ENV VITE_TIME_ZONE=${VITE_TIME_ZONE}

COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.25-alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
