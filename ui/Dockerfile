FROM node:16-buster
USER node
RUN mkdir /home/node/ui
WORKDIR /home/node/ui
COPY . .
RUN mkdir -p /home/node/ui/node_modules && \
    yarn install && \
    npm run build
CMD npm run start
