<template lang="pug">
  .container
    .grid.grid-cols-5.gap-4.mb-4(style="min-height: 400px;")
      Card.summary.col-span-2.m-2
        template(#content)
          .summary-title-main Current Value
          .summary-value-main {{ formatCurrency(summary.totalValue) }}
          .grid.grid-cols-2.gap-8.mt-16
            .col-span-1
              .summary-title-sub Invested
              .summary-value-sub {{ formatCurrency(summary.totalInvested) }}
            .col-span-1
              .summary-title-sub No. of Funds
              .summary-value-sub {{ schemes.length }}
            .col-span-1
              .summary-title-sub Current Return
              .flex.flex-row.justify-center.items-center
                .text-white.text-xl.mr-2 {{ formatCurrency(summary.totalChange.A)  }}
                template(v-if="summary.totalChange.A >= 0")
                  .text-white.text-sm.text-green-400.font-medium +{{ formatPct(summary.totalChangePct.A) }}
                template(v-else)
                  .text-white.text-sm.text-red-400.font-medium -{{ formatPct(summary.totalChangePct.A) }}
            .col-span-1
              .summary-title-sub 1 Day Change
              .flex.flex-row.justify-center.items-center
                .text-white.text-xl.mr-2 {{ formatCurrency(summary.totalChange.D)  }}
                template(v-if="summary.totalChange.D >= 0")
                  .text-white.text-sm.text-green-400.font-medium +{{ formatPct(summary.totalChangePct.D) }}
                template(v-else)
                  .text-white.text-sm.text-red-400.font-medium {{ formatPct(summary.totalChangePct.D) }}
            .col-span-1
              .summary-title-sub Absolute XIRR
              .summary-value-sub {{ formatPct(summary.xirr.overall) }}
            .col-span-1
              .summary-title-sub Current XIRR
              .summary-value-sub {{ formatPct(summary.xirr.current) }}
        template(#footer)
          .w-full.text-right.text-gray-400.text-sm(:class="{'invisible': summary.portfolioDate === ''}") NAV date: {{ summary.portfolioDate }}
      highchart.col-span-3.m-2(:modules="['drilldown']" :options="pieOptions" @chartLoaded="pieChartLoaded")
    highstock.w-full(:options="options"
      :update="['options.title', 'options.series']"
      :animation="{duration: 1000}"
      @chartLoaded="chartLoaded")
    //DataView.mt-4(:value="schemes" layout="list")
      template(#header)
        //.grid.grid-cols-10.gap-4.p-4
          .col-span-2
            .flex.flex-col.items-center
              .text-xl.text-gray-500.font-medium.uppercase Current Value
              .text-2xl.font-medium {{ formatCurrency(totalValue) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Invested
              .text-base {{ formatCurrency(totalInvested) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Day Change
              .text-base {{ formatCurrency(totalChange) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Total Return
              .text-base {{ formatCurrency(totalValue - totalInvested) }}
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
                    ProgressBar.flex-grow(:value="100*slotProps.data.value/totalValue" style="height: 0.5em" :showValue="false")
                    .text-sm.text-left.ml-2 {{ (100*slotProps.data.value/totalValue).toFixed(2) }}%
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
  onBeforeUnmount,
  onMounted,
  computed,
  reactive,
  ref,
  useContext,
  wrapProperty,
} from "@nuxtjs/composition-api";
import {
  DrilldownOptions,
  SeriesLineOptions,
  Options,
  SeriesPieOptions,
} from "highcharts";

import { Summary, Scheme } from "~/definitions/mutualfunds";
import { Chart } from "~/definitions/charts";
import { preparePieChartData, AllocationPieChartData } from "~/utils";

export const useAccessor = wrapProperty("$accessor", false);

export default defineComponent({
  setup() {
    const {
      $axios,
      app: { $bus },
    } = useContext();

    const accessor = useAccessor();

    const chart = ref<Chart | null>(null);
    const options = reactive<Options>({
      chart: {
        backgroundColor: "#edf0f5",
      },
      title: {
        text: "Portfolio Performance",
      },
      colors: [
        "#4CAF50",
        "#666666",
        "#058DC7",
        "#ED561B",
        "#DDDF00",
        "#24CBE5",
        "#64E572",
        "#FF9655",
        "#FFF263",
        "#6AF9C4",
      ],
      credits: {
        enabled: false,
      },
      lang: {
        thousandsSep: ",",
      },
      legend: {
        enabled: false,
      },
      navigator: {
        xAxis: {
          labels: {
            style: {
              fontWeight: "bold",
            },
          },
        },
      },
      plotOptions: {
        series: {
          animation: {
            duration: 1000,
          },
        },
      },
      rangeSelector: {
        allButtonsEnabled: true,
        buttonTheme: {
          fill: "none",
          stroke: "none",
          r: 3,
          style: {
            color: "#4CAF50",
            fontWeight: "bold",
          },
          states: {
            hover: {},
            select: {
              fill: "#4CAF50",
              style: {
                color: "white",
              },
            },
          },
        },
        inputStyle: {
          color: "#4CAF50",
          fontWeight: "bold",
        },
        selected: 4,
      },
      tooltip: {
        shared: true,
        useHTML: true,
      },
      xAxis: {
        labels: {
          style: {
            fontWeight: "bold",
          },
        },
      },
      yAxis: {
        labels: {
          style: {
            fontWeight: "bold",
          },
        },
      },
      series: [
        { name: "Current Value", data: [], type: "line" },
        { name: "Invested", data: [], type: "line" },
      ] as Array<SeriesLineOptions>,
      // FIXME: The following property shouldn't be required here, but the chart crashes without it.
      drilldown: {
        series: [],
      },
    });

    const pieChart = ref<Chart | null>(null);
    const pieOptions = reactive<Options>({
      chart: {
        backgroundColor: "#edf0f5",
        type: "pie",
      },
      colors: [
        "#4CAF50",
        "#666666",
        "#058DC7",
        "#ED561B",
        "#DDDF00",
        "#24CBE5",
        "#64E572",
        "#FF9655",
        "#FFF263",
        "#6AF9C4",
      ],
      credits: {
        enabled: false,
      },
      lang: {
        thousandsSep: ",",
      },
      legend: {
        enabled: false,
      },
      plotOptions: {
        series: {
          animation: {
            duration: 1000,
          },
          dataLabels: {
            enabled: true,
            format: "{point.name}: {point.y:.2f}%",
          },
        },
      },
      title: {
        text: "",
      },
      tooltip: {
        valueDecimals: 2,
        valueSuffix: "%",
      },
      series: [
        {
          name: "Investments",
          // colorByPoint: true,
          data: [],
          type: "pie",
        },
      ] as Array<SeriesPieOptions>,
      drilldown: {
        series: [],
      },
    });

    const portfolios = computed(() => accessor.mutualfunds.portfolios);
    const currentPortfolio = computed(
      () => accessor.mutualfunds.currentPortfolio
    );

    const getPortfolio = async () => {
      // await accessor.mutualfunds.
      try {
        await accessor.mutualfunds.updatePortfolios(true);
        if (currentPortfolio.value.id !== -1) {
          chart.value?.showLoading();
          const { data } = await $axios.get(
            "/api/mutualfunds/portfolio/" +
              currentPortfolio.value!.id +
              "/history/"
          );
          (options.series as Array<SeriesLineOptions>)![0].data = data.value;
          (options.series as Array<SeriesLineOptions>)![1].data = data.invested;
          chart.value?.update({
            series: options.series,
          });
        }
      } finally {
        chart.value?.hideLoading();
      }
    };

    const schemes = computed<Array<Scheme>>(
      () => accessor.mutualfunds.schemes
    );
    const summary = computed<Summary>(() => accessor.mutualfunds.summary);
    // const schemes = ref<Array<Scheme>>([]);
    // const totalInvested = ref(0.0);
    // const totalValue = ref(0.0);
    // const totalChange = ref({
    //   D: 0.0,
    //   A: 0.0,
    // });
    // const totalChangePct = ref({
    //   D: 0.0,
    //   A: 0.0,
    // });
    // const portfolioDate = ref("");
    // const xirr = ref({ current: 0.0, overall: 0.0 });
    const schemesLoading = ref(false);
    const getSchemes = async () => {
      if (currentPortfolio.value.id < 0) return;
      try {
        schemesLoading.value = true;
        pieChart.value?.showLoading();
        await accessor.mutualfunds.updateSchemes(false);
        const pieChartData: AllocationPieChartData = preparePieChartData(
          schemes.value,
          summary.value.totalValue,
        );

        (pieOptions.series as Array<SeriesPieOptions>)![0].data =
          pieChartData.series;
        (pieOptions.drilldown as DrilldownOptions)!.series =
          pieChartData.drilldown;
        pieChart.value?.update(
          {
            series: pieOptions.series,
            drilldown: {
              series: pieOptions.drilldown!.series,
            },
          },
          true,
          true
        );
      } finally {
        schemesLoading.value = false;
        pieChart.value?.hideLoading();
      }
    };

    const init = async () => {
      await getPortfolio();
      await getSchemes();
      $bus.$on("menu-toggle", reflow);
    };

    const reflow = () => {
      setTimeout(() => {
        chart.value?.reflow();
      }, 201);
    };

    onMounted(init);
    onBeforeUnmount(() => {
      $bus.$off("menu-toggle", reflow);
    });

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

    const chartLoaded = (chartObj: Chart) => {
      chart.value = chartObj;
    };
    const pieChartLoaded = (chartObj: Chart) => {
      pieChart.value = chartObj;
    };

    return {
      options,
      pieOptions,
      schemes,
      portfolios,
      currentPortfolio,
      chartLoaded,
      pieChartLoaded,
      chart,
      formatCurrency,
      formatNumber,
      formatPct,
      summary,
    };
  },
  head: {
    title: "Dashboard",
  },
});
</script>

<style lang="scss">
@import "assets/layout/variables";

.summary-title-main {
  @apply text-sm w-full text-white text-center;
}
.summary-value-main {
  @apply text-2xl text-white text-center font-bold;
}

.summary-title-sub {
  @apply text-base w-full text-white text-center;
}
.summary-value-sub {
  @apply text-xl w-full text-white text-center;
}

.p-dataview {
  .p-dataview-content {
    background: $bodyBgColor;

    > .p-grid > div {
      @apply border-gray-400;
    }
  }
  .p-dataview-header {
    background: darken($bodyBgColor, 2%);
  }
}
.highcharts-loading {
  opacity: 1 !important;
  background: #edf0f5 !important;
}

.highcharts-loading-inner,
.highcharts-loading-inner::before,
.highcharts-loading-inner::after {
  background: #4caf50;
  -webkit-animation: load1 1s infinite ease-in-out;
  animation: load1 1s infinite ease-in-out;
  width: 1em;
  height: 4em;
}
.highcharts-loading-inner {
  display: block;
  color: #4caf50;
  text-indent: -9999em;
  margin: 0 auto;
  top: 50% !important;
  position: relative;
  font-size: 11px;
  -webkit-transform: translate3d(-50%, -50%, 0);
  -ms-transform: translate3d(-50%, -50%, 0);
  transform: translate3d(-50%, -50%, 0);
  -webkit-animation-delay: -0.16s;
  animation-delay: -0.16s;
}
.highcharts-loading-inner::before,
.highcharts-loading-inner::after {
  position: absolute;
  top: 0;
  content: "";
}
.highcharts-loading-inner::before {
  left: -1.5em;
  -webkit-animation-delay: -0.32s;
  animation-delay: -0.32s;
}
.highcharts-loading-inner::after {
  left: 1.5em;
}
@-webkit-keyframes load1 {
  0%,
  80%,
  100% {
    box-shadow: 0 0;
    height: 4em;
  }
  40% {
    box-shadow: 0 -2em;
    height: 5em;
  }
}
@keyframes load1 {
  0%,
  80%,
  100% {
    box-shadow: 0 0;
    height: 4em;
  }
  40% {
    box-shadow: 0 -2em;
    height: 5em;
  }
}

.summary.p-card {
  //background: darken(#edf0f5, 2%);
  //background: darken(#4caf50, 10%);
  @apply rounded-xl bg-gradient-to-tr from-gray-900 to-gray-500;

  color: white;
  //@apply bg-gradient-to-r from-gray-400 to-gray-300;
}
</style>
