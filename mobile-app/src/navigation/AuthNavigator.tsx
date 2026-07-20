import { createNativeStackNavigator } from "@react-navigation/native-stack";

import LoginScreen from "../screens/LoginScreen";
import LoggedInPlaceholderScreen from "../screens/LoggedInPlaceholderScreen";

export type AuthStackParamList = {
  Login: undefined;
  LoggedInPlaceholder: undefined;
};

const Stack = createNativeStackNavigator<AuthStackParamList>();

export default function AuthNavigator() {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="Login" component={LoginScreen} />
      <Stack.Screen name="LoggedInPlaceholder" component={LoggedInPlaceholderScreen} />
    </Stack.Navigator>
  );
}
