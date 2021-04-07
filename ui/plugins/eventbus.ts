import { Plugin } from "@nuxt/types";
import mitt from "mitt";

const emitter = mitt();

export interface mittBus {
  $emit: typeof emitter.emit;
  $on: typeof emitter.on;
  $off: typeof emitter.off;
}

declare module "vue/types/vue" {
  interface Vue {
    $bus: mittBus;
  }
}

declare module "@nuxt/types" {
  // nuxtContext.app.$myInjectedFunction inside asyncData, fetch, plugins, middleware, nuxtServerInit
  interface NuxtAppOptions {
    $bus: mittBus;
  }
  // nuxtContext.$myInjectedFunction
  interface Context {
    $bus: mittBus;
  }
}

const eventBusPlugin: Plugin = (_, inject) => {
  inject("bus", {
    $on: emitter.on,
    $off: emitter.off,
    $emit: emitter.emit,
  });
};

export default eventBusPlugin;
