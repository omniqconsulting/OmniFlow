import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import GenericEntityScreen, { type FieldDef } from "../components/GenericEntityScreen";
import { customersApi, type ContactEntity } from "../api/setup";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupCustomers">;

const FIELDS: FieldDef[] = [
  { key: "name", label: "Name", type: "text" },
  { key: "contact_person", label: "Contact Person", type: "text" },
  { key: "phone", label: "Phone", type: "text", keyboardType: "phone-pad" },
  { key: "email", label: "Email", type: "text", keyboardType: "email-address" },
  { key: "address", label: "Address", type: "text", multiline: true },
  { key: "notes", label: "Notes", type: "text", multiline: true },
  { key: "is_active", label: "Active", type: "switch" },
];

export default function CustomersScreen({ navigation }: Props) {
  return (
    <GenericEntityScreen<ContactEntity>
      title="Customers"
      subtitle="Accounts used across CRM, Sales & FMS"
      fields={FIELDS}
      defaultValues={{ name: "", contact_person: "", phone: "", email: "", address: "", notes: "", is_active: true }}
      api={customersApi}
      rowTitle={(c) => c.name}
      rowSubtitle={(c) => [c.contact_person, c.phone].filter(Boolean).join(" · ") || (c.is_active ? "Active" : "Inactive")}
      toFormValues={(c) => ({
        name: c.name, contact_person: c.contact_person ?? "", phone: c.phone ?? "", email: c.email ?? "",
        address: c.address ?? "", notes: c.notes ?? "", is_active: c.is_active,
      })}
      onBack={() => navigation.goBack()}
    />
  );
}
