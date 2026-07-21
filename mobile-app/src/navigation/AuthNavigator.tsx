import { createNativeStackNavigator } from "@react-navigation/native-stack";

import LoginScreen from "../screens/LoginScreen";
import HomeScreen from "../screens/HomeScreen";
import AttendanceScreen from "../screens/AttendanceScreen";
import TicketsScreen from "../screens/TicketsScreen";
import TicketDetailScreen from "../screens/TicketDetailScreen";
import SetupScreen from "../screens/SetupScreen";
import SetupNotificationsScreen from "../screens/SetupNotificationsScreen";
import BranchesScreen from "../screens/BranchesScreen";
import DepartmentsScreen from "../screens/DepartmentsScreen";
import EmployeesScreen from "../screens/EmployeesScreen";
import CustomersScreen from "../screens/CustomersScreen";
import VendorsScreen from "../screens/VendorsScreen";
import MaterialsScreen from "../screens/MaterialsScreen";
import ProductsScreen from "../screens/ProductsScreen";
import UomScreen from "../screens/UomScreen";
import CustomListsScreen from "../screens/CustomListsScreen";
import PerformanceScreen from "../screens/PerformanceScreen";
import DayStatusRulesScreen from "../screens/DayStatusRulesScreen";
import FlowsScreen from "../screens/FlowsScreen";
import NotificationsScreen from "../screens/NotificationsScreen";
import type { SessionUser } from "../api/auth";

export type AuthStackParamList = {
  Login: undefined;
  Home: { user: SessionUser; slug: string };
  Attendance: { user: SessionUser };
  Tickets: { user: SessionUser };
  TicketDetail: { user: SessionUser; ticketId: string };
  Notifications: { user: SessionUser };
  Setup: { user: SessionUser };
  SetupNotifications: { user: SessionUser };
  SetupBranches: { user: SessionUser };
  SetupDepartments: { user: SessionUser };
  SetupEmployees: { user: SessionUser };
  SetupCustomers: { user: SessionUser };
  SetupVendors: { user: SessionUser };
  SetupMaterials: { user: SessionUser };
  SetupProducts: { user: SessionUser };
  SetupUom: { user: SessionUser };
  SetupLists: { user: SessionUser };
  SetupPerformance: { user: SessionUser };
  SetupDayStatusRules: { user: SessionUser };
  SetupFlows: { user: SessionUser };
};

const Stack = createNativeStackNavigator<AuthStackParamList>();

export default function AuthNavigator() {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="Login" component={LoginScreen} />
      <Stack.Screen name="Home" component={HomeScreen} />
      <Stack.Screen name="Attendance" component={AttendanceScreen} />
      <Stack.Screen name="Tickets" component={TicketsScreen} />
      <Stack.Screen name="TicketDetail" component={TicketDetailScreen} />
      <Stack.Screen name="Setup" component={SetupScreen} />
      <Stack.Screen name="SetupNotifications" component={SetupNotificationsScreen} />
      <Stack.Screen name="SetupBranches" component={BranchesScreen} />
      <Stack.Screen name="SetupDepartments" component={DepartmentsScreen} />
      <Stack.Screen name="SetupEmployees" component={EmployeesScreen} />
      <Stack.Screen name="SetupCustomers" component={CustomersScreen} />
      <Stack.Screen name="SetupVendors" component={VendorsScreen} />
      <Stack.Screen name="SetupMaterials" component={MaterialsScreen} />
      <Stack.Screen name="SetupProducts" component={ProductsScreen} />
      <Stack.Screen name="SetupUom" component={UomScreen} />
      <Stack.Screen name="SetupLists" component={CustomListsScreen} />
      <Stack.Screen name="SetupPerformance" component={PerformanceScreen} />
      <Stack.Screen name="SetupDayStatusRules" component={DayStatusRulesScreen} />
      <Stack.Screen name="SetupFlows" component={FlowsScreen} />
      <Stack.Screen name="Notifications" component={NotificationsScreen} />
    </Stack.Navigator>
  );
}
