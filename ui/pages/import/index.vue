<template lang="pug">
  Card.mx-5
    template(#title) Upload PDF
    template(#subtitle) CAS,Karvy
    template(#content)
      FileUpload.px-4(name="cas[]", url="/api/casparser" accept=".pdf" ref="fileUploader")
        template(#empty)
          p Drag and drop CAS pdf file here or click the "Choose" button above
      .flex.items-center.sm_w-full.pt-4(class="lg_w-3/6")
        .p-inputgroup.px-4.block
          InputText(:type="viewPassword ? 'text': 'password'" v-model="filePassword" placeholder="Password" :class="{'p-invalid': $v.filePassword.$error}")
          span.p-inputgroup-addon(@click="viewPassword = !viewPassword")
            i.pi(:class="viewPassword ? 'pi-eye' : 'pi-eye-slash'")
      .px-4.p-error
        small(v-if="$v.filePassword.required.$invalid") Password is missing!
        small(v-else-if="$v.filePassword.minLength.$invalid") Password is too short!
        small(v-else-if="hasError") Please select a file
    template(slot="footer")
      ProgressBar.my-2(mode="indeterminate" style="height: .25em" :class="{'invisible': !loading}")
      .flex.justify-between
        .p-error.px-4.font-bold(:class="{'invisible': error.length === 0}") {{ error }}
        Button(label="Upload" @click="nextPage" icon="pi pi-angle-right" iconPos="right" :disabled="hasError || loading")
</template>

<script lang="ts">
import {
  defineComponent,
  computed,
  ref,
  useContext,
} from "@nuxtjs/composition-api";
import { useVuelidate } from "@vuelidate/core";
import { minLength, required } from "@vuelidate/validators";
import { Ref } from "vue-demi";

interface FileUploader {
  hasFiles: boolean;
  files: FileList;
}

export default defineComponent({
  setup(_, { emit }) {
    const { $axios } = useContext();

    const fileUploader: Ref<FileUploader | null> = ref(null);
    const loading = ref(false);
    const error = ref("");

    const filePassword = ref("");
    const viewPassword = ref(false);
    const rules = {
      filePassword: { required, minLength: minLength(5) },
    };
    const $v = useVuelidate(rules, { filePassword });

    const hasError = computed(() => {
      return (
        $v.value.$invalid ||
        !(fileUploader.value && fileUploader.value!.hasFiles)
      );
    });

    const pageIndex = 0;
    const nextPage = () => {
      $v.value.$touch();
      if ($v.value.$invalid) return;
      if (fileUploader.value == null || !fileUploader.value!.hasFiles) return;

      const data = new FormData();
      data.append("file", (fileUploader.value!.files as FileList)[0]);
      data.append("password", filePassword.value);

      loading.value = true;
      error.value = "";
      $axios
        .post("/api/casparser", data, {
          headers: { "Content-Type": "multipart/form-data" },
        })
        .then(({ data }) => {
          if (data.status !== "OK") {
            error.value = data.message;
          } else {
            emit("next-page", { pdfData: data.data, pageIndex });
          }
          loading.value = false;
        })
        .catch((err) => {
          loading.value = false;
          if (err.response) {
            const response = err.response;
            if (response.status_code === 400) {
              error.value = "Invalid parameters";
            } else {
              error.value = response.data.message || "Unknown server error";
            }
          } else {
            error.value = err.message;
          }
        });
    };

    return {
      filePassword,
      viewPassword,
      fileUploader,
      hasError,
      loading,
      error,
      nextPage,
      $v,
    };
  },
});
</script>

<style lang="scss">
.p-fileupload-choose {
  & + button {
    display: none;
  }
  & ~ button:nth-of-type(2) {
    display: none;
  }
}

.p-fileupload-row {
  & > div:nth-of-type(1) {
    width: 60%;
  }
  & > div:nth-of-type(2),
  div:nth-of-type(3) {
    width: 20%;
  }
}
</style>
