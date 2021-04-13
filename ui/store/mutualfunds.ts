import { actionTree, getterTree, mutationTree } from "typed-vuex";
import { MFPortfolio, Scheme, Summary } from "~/definitions/mutualfunds";

export const state = () => ({
  portfolios: [] as Array<MFPortfolio>,
  currentPortfolio: {
    id: -1,
    name: "",
    email: "",
    pan: "",
  } as MFPortfolio,
  schemes: [] as Array<Scheme>,
  summary: {
    totalValue: 0.0,
    totalInvested: 0.0,
    xirr: { current: 0.0, overall: 0.0 },
    totalChange: { D: 0.0, A: 0.0 },
    totalChangePct: { D: 0.0, A: 0.0 },
    portfolioDate: "",
  } as Summary,
});

export type MutualfundsState = ReturnType<typeof state>;

export const getters = getterTree(state, {
  portfolios: (state: MutualfundsState) => state.portfolios,
  currentPortfolio: (state: MutualfundsState) => state.currentPortfolio,
  schemes: (state: MutualfundsState) => state.schemes,
  summary: (state: MutualfundsState) => state.summary,
});

export const mutations = mutationTree(state, {
  UPDATE_PORTFOLIOS: (
    state: MutualfundsState,
    newPortfolios: Array<MFPortfolio>
  ) => (state.portfolios = newPortfolios),
  UPDATE_CURRENT_PORTFOLIO: (
    state: MutualfundsState,
    newPortfolio: MFPortfolio
  ) => (state.currentPortfolio = newPortfolio),
  UPDATE_SCHEMES: (state: MutualfundsState, newSchemes: Array<Scheme>) =>
    (state.schemes = newSchemes),
  UPDATE_SUMMARY: (state: MutualfundsState, newSummary: Summary) =>
    (state.summary = newSummary),
});

export const actions = actionTree(
  { state, getters, mutations },
  {
    selectPortfolio({ commit }, portfolio: MFPortfolio) {
      commit("UPDATE_CURRENT_PORTFOLIO", portfolio);
    },
    async updatePortfolios({ commit, getters }, force = false) {
      if (getters.currentPortfolio.id === -1 || force) {
        const portfolios = (await this.$axios.$get(
          "/api/mutualfunds/portfolio/"
        )) as Array<MFPortfolio>;
        commit("UPDATE_PORTFOLIOS", portfolios);
        if (portfolios.length > 0 && getters.currentPortfolio.id < 0) {
          commit("UPDATE_CURRENT_PORTFOLIO", portfolios[0]);
        }
      }
    },
    async updateSchemes({ commit, dispatch, getters }, force = false) {
      if (getters.schemes.length > 0 && !force) return;
      if (getters.currentPortfolio.id === -1) {
        await dispatch("updatePortfolios", true);
      }
      if (getters.currentPortfolio.id === -1) return;
      const { data } = await this.$axios.get(
        "/api/mutualfunds/portfolio/" +
          getters.currentPortfolio.id +
          "/summary/"
      );
      commit("UPDATE_SCHEMES", data.schemes);
      commit("UPDATE_SUMMARY", {
        totalInvested: data.invested,
        totalChange: data.change,
        totalChangePct: data.change_pct,
        xirr: data.xirr,
        totalValue: data.value,
        portfolioDate: data.date,
      });
    },
  }
);
