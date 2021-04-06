<template lang="pug">
  .container
    highstock.w-full(:options="options",
      :update="['options.title', 'options.series']"
      :animation="{duration: 1000}"
      @chartLoaded="chartLoaded")
    //div.card
      Logo
      h1.title folioman
      .links
        a.button--green(href="https://nuxtjs.org/" target="_blank" rel="noopener noreferrer") Documentation
        a.button--grey(href="https://github.com/nuxt/nuxt.js" target="_blank" rel="noopener noreferrer") GitHub
</template>

<script lang="ts">
import {
  defineComponent,
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
}
// const { getOptions, pick, isNumber } = Highcharts;
// Highcharts.numberFormat = function (
//   number,
//   decimals,
//   decimalPoint,
//   thousandsSep
// ) {
//   number = +number || 0;
//   decimals = +decimals;
//
//   const lang = getOptions().lang!;
//   const origDec = (number.toString().split(".")[1] || "").length;
//   let decimalComponent;
//   const absNumber = Math.abs(number);
//   let ret;
//
//   if (decimals === -1) {
//     decimals = Math.min(origDec, 20); // Preserve decimals. Not huge numbers (#3793).
//   } else if (!isNumber(decimals)) {
//     decimals = 2;
//   }
//
//   // A string containing the positive integer component of the number
//   const strinteger = String(parseInt(absNumber.toFixed(decimals)));
//
//   // Leftover after grouping into thousands. Can be 0, 1 or 3.
//   const thousands = strinteger.length > 3 ? (strinteger.length - 1) % 2 : 0;
//
//   // Language
//   decimalPoint = pick(decimalPoint, lang.decimalPoint);
//   thousandsSep = pick(thousandsSep, lang.thousandsSep);
//
//   // Start building the return
//   ret = number < 0 ? "-" : "";
//
//   // Add the leftover after grouping into thousands. For example, in the number 42 000 000,
//   // this line adds 42.
//   ret += thousands ? strinteger.substr(0, thousands) + thousandsSep : "";
//
//   // Add the remaining thousands groups, joined by the thousands separator
//   ret += strinteger
//     .substr(thousands)
//     .replace(/(\d{2})(?=\d{3})/g, "$1" + thousandsSep);
//
//   // Add the decimal point and the decimal component
//   if (decimals) {
//     // Get the decimal component, and add power to avoid rounding errors with float numbers (#4573)
//     decimalComponent = Math.abs(
//       absNumber -
//         parseFloat(strinteger) +
//         Math.pow(10, -Math.max(decimals, origDec) - 1)
//     );
//     ret += decimalPoint + decimalComponent.toFixed(decimals).slice(2);
//   }
//
//   return ret;
// };

export default defineComponent({
  setup() {
    const { $axios } = useContext();
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
        const { data } = await $axios.get("/api/mutualfunds/portfolio");
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
    onMounted(getPortfolio);

    const chartLoaded = (chartObj: Chart) => {
      chart.value = chartObj;
    };

    return { options, chartLoaded, chart };
  },
  head: {
    title: "Dashboard",
  },
});
</script>

<style lang="scss">
//.container {
//  margin: 0 auto;
//  min-height: 100vh;
//  display: flex;
//  justify-content: center;
//  align-items: center;
//  text-align: center;
//}

.title {
  font-family: "Quicksand", "Source Sans Pro", -apple-system, BlinkMacSystemFont,
    "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  display: block;
  font-weight: 300;
  font-size: 100px;
  color: #35495e;
  letter-spacing: 1px;
}

.subtitle {
  font-weight: 300;
  font-size: 42px;
  color: #526488;
  word-spacing: 5px;
  padding-bottom: 15px;
}

.links {
  padding-top: 15px;
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
