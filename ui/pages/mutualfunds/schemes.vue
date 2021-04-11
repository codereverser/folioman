<template lang="pug">
  .container
    DataView.mt-4(:value="schemes" layout="list")
      template(#header)
        .grid.grid-cols-10.gap-4.p-4
          .col-span-2
            .flex.flex-col.items-center
              .text-xl.text-gray-500.font-medium.uppercase Current Value
              .text-2xl.font-medium {{ formatCurrency(summary.totalValue) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Invested
              .text-base {{ formatCurrency(summary.totalInvested) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Day Change
              .text-base {{ formatCurrency(summary.totalChange) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Total Return
              .text-base {{ formatCurrency(summary.totalChange.A) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Funds
              .text-base {{ schemes.length }}
        .grid.grid-cols-12.gap-4.p-4
          .col-span-6 Fund
          .col-span-2.text-right Value
          .col-span-2.text-right Invested
          .col-span-2.text-right Return
      template(#list="slotProps")
        .grid.grid-cols-12.gap-4.p-4
            .col-span-6
              .text-xl.capitalize.text-gray-500.font-medium {{ slotProps.data.name }}
              .grid.grid-cols-12
                .col-span-8
                  .flex.flex-row.items-center.my-2
                    .text-base.fonte-medium.text-gray-500 Units
                    .text-base.ml-2 {{ slotProps.data.units }}
                    .text-2xl.mx-2.text-gray-500.font-semibold •
                    ProgressBar.flex-grow(:value="100*slotProps.data.value/summary.totalValue" style="height: 0.5em" :showValue="false")
                    .text-sm.text-left.ml-2 {{ (100*slotProps.data.value/summary.totalValue).toFixed(2) }}%
              //.grid.grid-cols-12.my-2.items-center
                .col-span-12
                .text-sm.text-left {{ (100*slotProps.data.value/totalValue).toFixed(2) }}%
                ProgressBar(:value="100*slotProps.data.value/totalValue" style="height: 0.5em" :showValue="false")
                .text-lg.mx-2.text-gray-500.font-semibold •
                .text-sm Units {{slotProps.data.units}}
              //.flex.flex-row.items-center.my-2
                .flex.flex-row
                  .font-medium.text-base.text-gray-500 NAV
                  .text-base.ml-2 {{ slotProps.data.nav0 }}
                .text-2xl.mx-2.text-gray-500.font-semibold •
                .flex.flex-row
                  .font-medium.text-base.text-gray-500 Avg NAV
                  .text-base.ml-2 {{ slotProps.data.avg_nav }}
                .text-lg.mx-2.text-gray-500.font-semibold •
                .flex.flex-row
                  .font-medium.text-base.text-gray-500 1D Change
                  .text-base.ml-2 {{ formatCurrency(slotProps.data.change) }}
            .col-span-2
              .text-lg.text-right {{ formatCurrency(slotProps.data.value) }}
              .flex.flex-row.my-2.justify-end
                .flex.flex-row.items-center
                  .font-medium.text-xs.text-gray-500 NAV
                  .text-sm.ml-2 {{ slotProps.data.nav0 }}
            .col-span-2
              .text-lg.font-medium.text-right {{ formatCurrency(slotProps.data.invested) }}
              .flex.flex-row.my-2.justify-end
                .flex.flex-row.items-center
                  .font-medium.text-xs.text-gray-500 avg
                  .text-sm.ml-2 {{ slotProps.data.avg_nav }}
            .col-span-2
              .text-lg.text-right {{ formatCurrency(slotProps.data.value - slotProps.data.invested) }}
              .text-sm.text-right.my-2 {{ (100 * (slotProps.data.value - slotProps.data.invested)/slotProps.data.invested).toFixed(2) }}%
</template>

<script lang="ts">
import {
  defineComponent,
  onMounted,
  computed,
  ref,
  useContext,
  wrapProperty,
} from "@nuxtjs/composition-api";

import { Summary, Scheme } from "~/definitions/mutualfunds";

export const useAccessor = wrapProperty("$accessor", false);

export default defineComponent({
  setup() {
    const accessor = useAccessor();

    const schemes = computed<Array<Scheme>>(() => accessor.mutualfunds.schemes);
    const summary = computed<Summary>(() => accessor.mutualfunds.summary);
    const schemesLoading = ref(false);
    const getSchemes = async () => {
      try {
        await accessor.mutualfunds.updateSchemes(false);
      } finally {
        schemesLoading.value = false;
      }
    };

    const init = async () => {
      await getSchemes();
    };

    onMounted(init);

    const formatCurrency = (num: Number) => {
      return num.toLocaleString("en-IN", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
        style: "currency",
        currency: "INR",
      });
    };

    const formatNumber = (num: number | null, digits = 2): string => {
      return num?.toFixed(digits) || "N.A.";
    };

    const formatPct = (num: number | null, digits = 2): string => {
      return formatNumber(num, digits) + " %";
    };

    return {
      schemes,
      formatCurrency,
      formatNumber,
      formatPct,
      summary,
    };
  },
  head: {
    title: "Summary",
  },
});
</script>

<!--<style lang="scss">-->
<!--@import "assets/layout/variables";-->

<!--.summary-title-main {-->
<!--  @apply text-sm w-full text-white text-center;-->
<!--}-->
<!--.summary-value-main {-->
<!--  @apply text-2xl text-white text-center font-bold;-->
<!--}-->

<!--.summary-title-sub {-->
<!--  @apply text-base w-full text-white text-center;-->
<!--}-->
<!--.summary-value-sub {-->
<!--  @apply text-xl w-full text-white text-center;-->
<!--}-->

<!--.p-dataview {-->
<!--  .p-dataview-content {-->
<!--    background: $bodyBgColor;-->

<!--    > .p-grid > div {-->
<!--      @apply border-gray-400;-->
<!--    }-->
<!--  }-->
<!--  .p-dataview-header {-->
<!--    background: darken($bodyBgColor, 2%);-->
<!--  }-->
<!--}-->
<!--.highcharts-loading {-->
<!--  opacity: 1 !important;-->
<!--  background: #edf0f5 !important;-->
<!--}-->

<!--.highcharts-loading-inner,-->
<!--.highcharts-loading-inner::before,-->
<!--.highcharts-loading-inner::after {-->
<!--  background: #4caf50;-->
<!--  -webkit-animation: load1 1s infinite ease-in-out;-->
<!--  animation: load1 1s infinite ease-in-out;-->
<!--  width: 1em;-->
<!--  height: 4em;-->
<!--}-->
<!--.highcharts-loading-inner {-->
<!--  display: block;-->
<!--  color: #4caf50;-->
<!--  text-indent: -9999em;-->
<!--  margin: 0 auto;-->
<!--  top: 50% !important;-->
<!--  position: relative;-->
<!--  font-size: 11px;-->
<!--  -webkit-transform: translate3d(-50%, -50%, 0);-->
<!--  -ms-transform: translate3d(-50%, -50%, 0);-->
<!--  transform: translate3d(-50%, -50%, 0);-->
<!--  -webkit-animation-delay: -0.16s;-->
<!--  animation-delay: -0.16s;-->
<!--}-->
<!--.highcharts-loading-inner::before,-->
<!--.highcharts-loading-inner::after {-->
<!--  position: absolute;-->
<!--  top: 0;-->
<!--  content: "";-->
<!--}-->
<!--.highcharts-loading-inner::before {-->
<!--  left: -1.5em;-->
<!--  -webkit-animation-delay: -0.32s;-->
<!--  animation-delay: -0.32s;-->
<!--}-->
<!--.highcharts-loading-inner::after {-->
<!--  left: 1.5em;-->
<!--}-->
<!--@-webkit-keyframes load1 {-->
<!--  0%,-->
<!--  80%,-->
<!--  100% {-->
<!--    box-shadow: 0 0;-->
<!--    height: 4em;-->
<!--  }-->
<!--  40% {-->
<!--    box-shadow: 0 -2em;-->
<!--    height: 5em;-->
<!--  }-->
<!--}-->
<!--@keyframes load1 {-->
<!--  0%,-->
<!--  80%,-->
<!--  100% {-->
<!--    box-shadow: 0 0;-->
<!--    height: 4em;-->
<!--  }-->
<!--  40% {-->
<!--    box-shadow: 0 -2em;-->
<!--    height: 5em;-->
<!--  }-->
<!--}-->

<!--.summary.p-card {-->
<!--  //background: darken(#edf0f5, 2%);-->
<!--  //background: darken(#4caf50, 10%);-->
<!--  @apply rounded-xl bg-gradient-to-tr from-gray-900 to-gray-500;-->

<!--  color: white;-->
<!--  //@apply bg-gradient-to-r from-gray-400 to-gray-300;-->
<!--}-->
<!--</style>-->
