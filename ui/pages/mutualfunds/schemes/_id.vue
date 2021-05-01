<template lang="pug">
  .container
    .grid.grid-cols-12.gap-4.items-center
      .text-3xl.text-gray-500.font-semibold.capitalize.col-span-12 {{ scheme.name }}
      .text-sm.text-gray-400.font-semibold.uppercase.col-span-2 {{ scheme.category.main }} - {{ scheme.category.sub }}
      .text-sm.text-gray-400.font-semibold.uppercase.col-span-4
        .grid.grid-cols-12.items-center
          .text-gray-500.text-sm.font-medium.col-span-2.text-right NAV
          .text-gray-900.text-lg.font-medium.col-span-4.text-center {{ scheme.nav0 }}
          .text-gray-500.text-sm.font-medium.col-span-2.text-right 1D
          .text-lg.font-medium.col-span-4.text-center(:class="[scheme.nav0 > scheme.nav1 ? 'text-primary': 'text-red-500']") {{ (100 * (scheme.nav0 - scheme.nav1) / scheme.nav1).toFixed(2)  }}%
    .grid.mt-16.grid-cols-12.gap-4.items-center.align-center
      .col-span-3.text-center
        .text-base.font-semibold.text-gray-500.capitalize Invested
        .text-lg.font-semibold.text-gray-600 {{ formatCurrency(scheme.invested) }}
      .col-span-3.text-center
        .text-base.font-semibold.text-gray-500.capitalize Current Value
        .text-xl.font-semibold.text-gray-800 {{ formatCurrency(scheme.value) }}
      .col-span-3.text-center
        .text-base.font-semibold.text-gray-500.capitalize Total Units
        .text-lg.font-semibold.text-gray-800 {{ scheme.units }}
      .col-span-3.text-center
        .text-base.font-semibold.text-gray-500.capitalize Avg NAV
        .text-lg.font-semibold.text-gray-800 {{ scheme.avg_nav }}
    DataTable.mt-8.p-datatable-sm(:value="scheme.folios" :loading="loading")
      template(#header)
        .text-xl.text-secondary Folios
      Column(field="folio" header="Number")
      Column(field="invested" header="Invested")
        template(#body="slotProps") {{ formatCurrency(slotProps.data.invested) }}
      Column(field="value" header="Current Value")
        template(#body="slotProps") {{ formatCurrency(slotProps.data.invested) }}
      Column(field="units" header="Units")
      Column(field="avg_nav" header="Avg NAV")
    DataTable.mt-8(:value="transactions" :loading="loading" :autoLayout="true" :paginator="true" :rows="10")
      template(#header)
        .text-xl.text-secondary Transactions
      Column(field="date" header="Date" sortable header-class="w-32")
      Column(field="folio" header="Folio")
      Column(field="description" header="Description")
      Column(field="sub_type" header="Type")
      Column(field="amount" header="Amount" body-class="p-text-right")
      Column(field="nav" header="NAV" body-class="p-text-right")
      Column(field="units" header="Units" body-class="p-text-right")
</template>
<script lang="ts">
import {
  defineComponent,
  onMounted,
  ref,
  useContext,
  useRoute,
  wrapProperty,
} from "@nuxtjs/composition-api";

import { Scheme } from "@/definitions/mutualfunds";

export const useAccessor = wrapProperty("$accessor", false);

export default defineComponent({
  setup() {
    const accessor = useAccessor();
    const route = useRoute();
    const { id } = route.value.params;
    const { $axios } = useContext();

    const scheme = ref<Scheme>({
      avg_nav: 0,
      folios: [],
      invested: 1,
      nav0: 1,
      nav1: 1,
      units: 0,
      value: 1,
      id: -1,
      name: "",
      category: {
        main: "",
        sub: "",
      },
    });
    const transactions = ref([]);
    const loading = ref(false);
    const formatCurrency = (num: Number) => {
      return num.toLocaleString("en-IN", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
        style: "currency",
        currency: "INR",
      });
    };

    const init = async () => {
      try {
        loading.value = true;
        if (
          !Object.prototype.hasOwnProperty.call(
            accessor.mutualfunds.schemeData,
            id
          )
        ) {
          await accessor.mutualfunds.updateSchemes(false);
        }
        if (
          Object.prototype.hasOwnProperty.call(
            accessor.mutualfunds.schemeData,
            id
          )
        ) {
          scheme.value = accessor.mutualfunds.schemeData[id];
          transactions.value = await $axios.$post(
            "/api/mutualfunds/portfolio/transactions/",
            {
              portfolio_ids: [accessor.mutualfunds.currentPortfolio.id],
              fund: scheme.value.id,
            }
          );
        } else {
          // TODO: redirect to 404.
        }
      } catch {
        console.log("here");
      } finally {
        loading.value = false;
      }
    };
    onMounted(init);

    return { formatCurrency, scheme, transactions, loading };
  },
});
</script>

<style lang="scss">
.min-date-width {
  min-width: 80px !important;
}
</style>
