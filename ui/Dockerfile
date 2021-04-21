FROM node:lts-alpine
RUN apk add --no-cache --update g++ gcc make python3 && \
    rm -rf /var/cache/apk
WORKDIR /ui
COPY . .
RUN npm install && npm run build
