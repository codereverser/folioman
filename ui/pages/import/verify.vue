<template lang="pug">
  .mx-4
    .text-center.text-lg.font-semibold.mt-2 Statement Period {{ formData.pdfData.statement_period.from }} to {{ formData.pdfData.statement_period.to }}
    .flex.justify-between.my-4
      .flex
        span Name:&nbsp;
        .font-semibold {{ formData.pdfData.investor_info.name }}
      .flex
        span Email:&nbsp;
        .font-semibold {{ formData.pdfData.investor_info.email }}
    Panel.pb-4(v-for="(folio, index) in Object.values(formData.pdfData.folios)" :key="folio.folio" :toggleable="true" :collapsed="index > 0")
      template(#header)
        .p-panel-title(class="w-1/4") Folio: {{ folio.folio }}
        .p-panel-title(class="w-1/4") PAN: {{ folio.PAN }}
        .p-panel-title(class="w-1/4") KYC: {{ folio.KYC }}
        .p-panel-title(class="w-1/4") PANKYC: {{ folio.PANKYC }}
      div(v-for="scheme in folio.schemes" :key="scheme.scheme")
        DataTable.p-datatable-sm(:value="scheme.transactions" :paginator="scheme.transactions.length > 5" :rows="10")
          template(#header)
            .flex
              .font-semibold(class="w-4/6") {{ scheme.scheme }}
              .flex(class="w-1/6")
                span Open:&nbsp;
                .font-semibold {{ scheme.open }}
              .flex(class="w-1/6")
                span Close:&nbsp;
                .font-semibold {{ scheme.close }}
          Column(field="date" header="Date")
          Column(field="description" header="Description")
          Column(field="amount" header="Amount")
          Column(field="nav" header="NAV")
          Column(field="units" header="units")
</template>

<script lang="ts">
import { defineComponent, computed } from "@nuxtjs/composition-api";
import { ImportData } from "~/definitions/defs";

export default defineComponent({
  props: {
    formData: {
      type: Object as () => ImportData,
      required: true,
    },
  },
  setup({ formData }) {
  },
});
</script>
