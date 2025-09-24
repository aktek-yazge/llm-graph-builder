# Step 1: Build the React application
FROM node:24-alpine3.21 AS build

WORKDIR /app
COPY package.json yarn.lock ./
RUN yarn install
COPY . ./
RUN yarn run build

# Step 2: Serve the application using Nginx
FROM nginx:alpine
ARG DEPLOYMENT_ENV="local"
ENV DEPLOYMENT_ENV=$DEPLOYMENT_ENV
COPY --from=build /app/dist /usr/share/nginx/html
COPY /nginx/nginx.${DEPLOYMENT_ENV}.conf /etc/nginx/templates/nginx.conf.template

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]