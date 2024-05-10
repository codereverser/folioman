FROM node:16-buster
USER node
RUN mkdir /home/node/ui
WORKDIR /home/node/ui
COPY . .

USER root
RUN chown -R node:node /home/node/ui/.yarn && \
    chmod 777 /home/node/ui/.yarn

USER node

RUN mkdir -p /home/node/ui/node_modules && \
    yarn install && \
    npm run build
CMD npm run start
