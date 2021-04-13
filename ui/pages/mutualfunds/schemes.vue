<template lang="pug">
  .container
    DataTable.p-datatable(:value="schemes" :autoLayout="true" :paginator="true" :rows="10" :loading="schemesLoading")
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
              .text-base {{ formatCurrency(summary.totalChange.D) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Total Return
              .text-base {{ formatCurrency(summary.totalChange.A) }}
          .col-span-2
            .flex.flex-col.items-center
              .text-lg.text-gray-500.font-medium Funds
              .text-base {{ schemes.length }}

      Column(field="name" header="Fund" :sortable="true")
        template(#body="slotProps")
          .text-xl.capitalize.text-gray-500.font-semibold.hello {{ slotProps.data.name }}
          .grid.grid-cols-12
                .col-span-8
                  .flex.flex-row.items-center.my-2
                    .text-base.fonte-medium.text-gray-500 Units
                    .text-base.ml-2 {{ slotProps.data.units }}
                    .text-2xl.mx-2.text-gray-500.font-semibold •
                    ProgressBar.flex-grow(:value="100*slotProps.data.value/summary.totalValue" style="height: 0.5em" :showValue="false")
                    .text-sm.text-left.ml-2 {{ (100*slotProps.data.value/summary.totalValue).toFixed(2) }}%
      Column(field="value" header="Value" header-class="p-text-right" :sortable="true")
        template(#body="slotProps")
          .text-lg.text-right {{ formatCurrency(slotProps.data.value) }}
          .flex.flex-row.my-2.justify-end
                          .flex.flex-row.items-center
                            .font-medium.text-xs.text-gray-500 NAV
                            .text-sm.ml-2 {{ slotProps.data.nav0 }}
      Column(field="invested" header="invested" header-class="p-text-right" :sortable="true")
        template(#body="slotProps")
          .text-lg.text-right {{ formatCurrency(slotProps.data.invested) }}
          .flex.flex-row.my-2.justify-end
                .flex.flex-row.items-center
                  .font-medium.text-xs.text-gray-500 avg
                  .text-sm.ml-2 {{ slotProps.data.avg_nav }}
      Column(field="change.A" header="Returns" header-class="p-text-right" :sortable="true")
        template(#body="slotProps")
          .text-lg.text-right {{ formatCurrency(slotProps.data.change.A) }}
          .text-sm.text-right.my-2 {{ (100 * (slotProps.data.value - slotProps.data.invested)/slotProps.data.invested).toFixed(2) }}%
</template>

<script lang="ts">
import {
  defineComponent,
  onMounted,
  computed,
  ref,
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
        schemesLoading.value = true;
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
      schemesLoading,
    };
  },
  head: {
    title: "Summary",
  },
});
</script>

<style lang="scss">
.p-text-right {
  text-align: right !important;
}
</style>
