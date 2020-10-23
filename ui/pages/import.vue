<template lang="pug">
  div
    Card.m-5
      template(slot="content")
        Steps(:model="items.list")
    router-view(v-slot="{Component}" :formData="importData" @prev-page="prevPage($event)" @next-page="nextPage($event)" @complete="complete")
      keep-alive
        component(:is="Component")
</template>
<script lang="ts">
import { defineComponent, reactive } from "@nuxtjs/composition-api";
import { StepEvent, ImportData } from "@/definitions/defs";

export default defineComponent({
  setup(_, { root }) {
    const { $router } = root;
    const items = reactive({
      list: [
        {
          label: "Upload",
          to: "/import",
        },
        {
          label: "Verify",
          to: "/import/verify",
        },
      ],
    });

    const importData: ImportData = reactive({
      pdfData: null,
    });

    const nextPage = (event: StepEvent) => {
      if (Object.prototype.hasOwnProperty.call(event, "pdfData")) {
        importData.pdfData = event.pdfData!;
      }
      $router.push(items.list[event.pageIndex + 1].to);
    };

    const prevPage = (event: StepEvent) => {
      $router.push(items.list[event.pageIndex - 1].to);
    };
    const complete = () => {
      /* hello */
    };
    return { importData, items, nextPage, prevPage, complete };
  },
});
</script>
