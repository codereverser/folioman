import Vue from "vue";
import Button from "primevue/button";
import Card from "primevue/card";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import FileUpload from "primevue/fileupload";
import InputText from "primevue/inputtext";
import Panel from "primevue/panel";
import ProgressBar from "primevue/progressbar";
import ProgressSpinner from "primevue/progressspinner";
import Steps from "primevue/steps";
import Toast from "primevue/toast";
import ToastService from "primevue/toastservice";

Vue.use(ToastService);

Vue.component("Button", Button);
Vue.component("Card", Card);
Vue.component("Column", Column);
Vue.component("DataTable", DataTable);
Vue.component("FileUpload", FileUpload);
Vue.component("InputText", InputText);
Vue.component("Panel", Panel);
Vue.component("ProgressBar", ProgressBar);
Vue.component("ProgressSpinner", ProgressSpinner);
Vue.component("Steps", Steps);
Vue.component("Toast", Toast);
