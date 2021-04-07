<template lang="pug">
  .container
    highstock.w-full(:options="options"
      :update="['options.title', 'options.series']"
      :animation="{duration: 1000}"
      @chartLoaded="chartLoaded")
    DataView.mt-4(:value="schemes" layout="list")
      template(#header)
        .grid.grid-cols-10.gap-4.p-4
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
              .text-base {{ formatCurrency(total1DChange) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Total Return
              .text-base {{ formatCurrency(totalValue - totalInvested) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Funds
              .text-base {{ schemes.length }}
        .grid.grid-cols-12.gap-4.p-4.border-t-2.border-gray-300
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
  reactive,
  ref,
  useContext,
} from "@nuxtjs/composition-api";
import { SeriesLineOptions, Options } from "highcharts";

interface Chart {
  showLoading(): void;
  hideLoading(): void;
  update(arg0: Options): void;
  reflow(): void;
}

export default defineComponent({
  setup() {
    const { $axios, app } = useContext();
    const { $bus } = app;

    const chart = ref<Chart | null>(null);
    const options = reactive<Options>({
      chart: {
        backgroundColor: "#edf0f5",
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
    });

    const getPortfolio = async () => {
      try {
        if (chart.value) chart.value.showLoading();
        const { data } = await $axios.get("/api/mutualfunds/portfolio_history");
        (options.series as Array<SeriesLineOptions>)![0].data = data.value;
        (options.series as Array<SeriesLineOptions>)![1].data = data.invested;

        if (chart.value) {
          chart.value.hideLoading();
          chart.value.update({
            series: options.series,
          });
        }
      } catch (err) {
        if (chart.value) chart.value.hideLoading();
      }
    };

    const schemes = ref([]);
    const totalInvested = ref(0.0);
    const totalValue = ref(0.0);
    const total1DChange = ref(0.0);
    const schemesLoading = ref(false);
    const getSchemes = async () => {
      schemesLoading.value = true;
      try {
        const { data } = await $axios.get("/api/mutualfunds/portfolio_list");
        schemes.value = data.schemes;
        totalInvested.value = data.invested;
        total1DChange.value = data.change;
        totalValue.value = data.value;
        schemesLoading.value = false;
      } catch (err) {
        schemesLoading.value = false;
      }
    };

    const init = async () => {
      await getPortfolio();
      await getSchemes();
      $bus.$on("menu-toggle", reflow);
    };

    const reflow = () => {
      if (chart.value) {
        setTimeout(() => {
          chart.value!.reflow();
        }, 201);
      }
    };

    onMounted(init);
    onBeforeUnmount(() => {
      $bus.$off("menu-toggle", reflow);
    });

    const formatCurrency = (num: Number) => {
      return num.toLocaleString("en-IN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
        style: "currency",
        currency: "INR",
      });
    };

    const chartLoaded = (chartObj: Chart) => {
      chart.value = chartObj;
    };

    return {
      options,
      schemes,
      chartLoaded,
      chart,
      formatCurrency,
      totalInvested,
      total1DChange,
      totalValue,
    };
  },
  head: {
    title: "Dashboard",
  },
});
</script>

<style lang="scss">
@import "assets/layout/variables";

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
</style>
