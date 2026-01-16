# Development mode for staging (temporarily skip build due to Utils.ts issue)
FROM node:24-alpine3.21

ARG DEPLOYMENT_ENV="local"
# DEPLOYMENT_ENV'i runtime'da da kullanılabilir yap (vite.config.ts base path için)
ENV DEPLOYMENT_ENV=${DEPLOYMENT_ENV}

WORKDIR /app
COPY package.json yarn.lock ./
RUN yarn install

# Copy all env files and select the right one
COPY .env* ./
RUN if [ "$DEPLOYMENT_ENV" = "staging" ] && [ -f ".staging.env" ]; then \
        cp .staging.env .env; \
    fi

COPY . ./

EXPOSE 5173
# Run dev server instead of build (temporary workaround)
CMD ["yarn", "dev", "--host", "0.0.0.0", "--port", "5173"]