# Development mode for staging (temporarily skip build due to Utils.ts issue)
FROM node:24-alpine3.21

ARG DEPLOYMENT_ENV="local"
WORKDIR /app
COPY package.json yarn.lock ./
RUN yarn install

# Copy all env files and select the right one
COPY .env* ./
RUN if [ "$DEPLOYMENT_ENV" = "staging" ] && [ -f ".staging.env" ]; then \
        cp .staging.env .env; \
    fi

COPY . ./

EXPOSE 8080
# Run dev server instead of build (temporary workaround)
CMD ["yarn", "dev", "--host", "0.0.0.0", "--port", "8080"]