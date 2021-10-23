FROM node:16-buster
WORKDIR /ui
COPY . .
RUN yarn install && npm run build
CMD npm run start
