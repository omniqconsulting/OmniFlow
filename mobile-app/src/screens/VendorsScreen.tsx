import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import GenericEntityScreen, { type FieldDef } from "../components/GenericEntityScreen";
import { vendorsApi, type ContactEntity } from "../api/setup";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupVendors">;

const FIELDS: FieldDef[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "contact_person", label: "Contact Person", type: "text" },
  { key: "phone", label: "Phone", type: "text", keyboardType: "phone-pad" },
  { key: "email", label: "Email", type: "text", keyboardType: "email-address" },
  { key: "address", label: "Address", type: "text", multiline: true },
  { key: "notes", label: "Parts Supplied / Notes", type: "text", multiline: true },
  { key: "is_active", label: "Active", type: "switch" },
];

export default function VendorsScreen({ navigation }: Props) {
  return (
    <GenericEntityScreen<ContactEntity>
      title="Vendors"
      subtitle="Suppliers used across FMS & Purchasing"
      fields={FIELDS}
      defaultValues={{ name: "", contact_person: "", phone: "", email: "", address: "", notes: "", is_active: true }}
      api={vendorsApi}
      rowTitle={(v) => v.name}
      rowSubtitle={(v) => [v.contact_person, v.phone].filter(Boolean).join(" · ") || (v.is_active ? "Active" : "Inactive")}
      toFormValues={(v) => ({
        name: v.name, contact_person: v.contact_person ?? "", phone: v.phone ?? "", email: v.email ?? "",
        address: v.address ?? "", notes: v.notes ?? "", is_active: v.is_active,
      })}
      onBack={() => navigation.goBack()}
    />
  );
}
