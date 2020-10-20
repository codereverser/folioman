import { reactive, toRefs } from "@nuxtjs/composition-api";

const state = reactive({
  isOpen: false,
});

export function useSidebar() {
  return {
    ...toRefs(state),
  };
}
