<template lang="pug">
  Card.mx-4(v-if="formData.pdfData")
    template(#title)
      .text-center.text-lg.font-semibold.mt-2 Statement Period {{ formData.pdfData.statement_period.from }} to {{ formData.pdfData.statement_period.to }}
    template(#subtitle)
      .flex.justify-between.my-4
        .flex
          span Name:&nbsp;
          .font-semibold {{ formData.pdfData.investor_info.name }}
        .flex
          span Email:&nbsp;
          .font-semibold {{ formData.pdfData.investor_info.email }}
    template(#content)
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
            Column(field="amount" header="Amount" body-class="p-text-right")
            Column(field="nav" header="NAV" body-class="p-text-right")
            Column(field="units" header="Units" body-class="p-text-right")
            Column(field="balance" header="Balance" body-class="p-text-right")
    template(#footer)
      ProgressBar.my-2(mode="indeterminate" style="height: .25em" :class="{'invisible': !loading}")
      .flex.justify-between
        Button(label="Back" @click="prevPage" icon="pi pi-angle-left" :disabled="loading" iconPost="left")
        Button(label="Import" @click="importData" :disabled="loading" icon="pi pi-upload" iconPost="right")
</template>

<script lang="ts">
import { defineComponent, ref, useContext } from "@nuxtjs/composition-api";
import { ImportData } from "@/definitions/defs";

export default defineComponent({
  props: {
    formData: {
      type: Object as () => ImportData,
      required: true,
    },
  },
  setup({ formData }, { root, emit }) {
    const { $router } = root;
    const pageIndex = 1;

    const { $toast } = root;
    const { $axios } = useContext();

    if (!(formData && formData.pdfData)) {
      $router.push("/import");
    }

    const loading = ref(false);

    const prevPage = () => {
      emit("prev-page", { pageIndex });
    };
    const importData = () => {
      loading.value = true;
      $axios
        .post("/api/cas/import", { data: formData.pdfData })
        .then(({ data }) => {
          loading.value = false;
          // eslint-disable-next-line camelcase
          const { num_folios, transactions } = data;
          const { total, added } = transactions;
          $toast.add({
            severity: "success",
            summary: "Import success!",
            detail:
              "New folios: " +
              // eslint-disable-next-line camelcase
              num_folios +
              " :: Transactions imported - " +
              added +
              " / " +
              total,
            life: 5000,
          });
          $router.push("/");
        })
        .catch((error) => {
          let detail = "Unknown error";
          if (error.response) {
            const { data } = error.response;
            if (error.response.status === 400) {
              detail = data.detail;
            } else {
              detail = data.message;
            }
          }
          $toast.add({
            severity: "error",
            summary: "Error",
            detail,
            life: 5000,
          });
          loading.value = false;
        });
    };

    return { prevPage, importData, loading };
  },
  head: {
    title: "Verify CAS",
  },
});
</script>

<style lang="scss">
.p-text-right {
  text-align: right !important;
}
</style>
