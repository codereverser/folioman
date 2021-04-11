import {
  actionTree,
  getterTree,
  getAccessorType,
  mutationTree,
} from "typed-vuex";

import * as mutualfunds from "~/store/mutualfunds";

export const state = () => ({});

export type RootState = ReturnType<typeof state>;

export const getters = getterTree(state, {});

export const mutations = mutationTree(state, {});

export const actions = actionTree({ state, getters, mutations }, {});

export const accessorType = getAccessorType({
  state,
  getters,
  mutations,
  actions,
  modules: {
    // The key (submodule) needs to match the Nuxt namespace (e.g. ~/store/submodule.ts)
    mutualfunds,
  },
});
