import React from "react";
import { Platform, TouchableOpacity } from "react-native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";

import DashboardScreen from "../screens/DashboardScreen";
import FaturasScreen from "../screens/FaturasScreen";
import EmpresasScreen from "../screens/EmpresasScreen";
import CadastrarEmpresaScreen from "../screens/CadastrarEmpresaScreen";

const Tab = createBottomTabNavigator();
const EmpresasStackNav = createNativeStackNavigator();

function EmpresasStack() {
  return (
    <EmpresasStackNav.Navigator
      screenOptions={{
        headerStyle: {
          backgroundColor: "#1a56db",
        },
        headerTintColor: "#fff",
        headerTitleStyle: { fontWeight: "600", fontSize: 17 },
      }}
    >
      <EmpresasStackNav.Screen
        name="EmpresasList"
        component={EmpresasScreen}
        options={({ navigation }) => ({
          title: "Empresas",
          headerRight: () => (
            <TouchableOpacity
              onPress={() => navigation.navigate("CadastrarEmpresa")}
              style={{ marginRight: 8 }}
            >
              <Ionicons name="add-circle" size={28} color="#fff" />
            </TouchableOpacity>
          ),
        })}
      />
      <EmpresasStackNav.Screen
        name="CadastrarEmpresa"
        component={CadastrarEmpresaScreen}
        options={({ route }: any) => ({
          title: route.params?.empresa ? "Editar Empresa" : "Cadastrar Empresa",
        })}
      />
    </EmpresasStackNav.Navigator>
  );
}

export default function AppNavigator() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerStyle: {
          backgroundColor: "#1a56db",
          elevation: 0,
          shadowOpacity: 0,
        },
        headerTintColor: "#fff",
        headerTitleStyle: { fontWeight: "600", fontSize: 17 },
        tabBarActiveTintColor: "#1a56db",
        tabBarInactiveTintColor: "#9ca3af",
        tabBarStyle: {
          paddingBottom: Platform.OS === "ios" ? 20 : 6,
          paddingTop: 6,
          height: Platform.OS === "ios" ? 80 : 60,
          borderTopWidth: 1,
          borderTopColor: "#e5e7eb",
          elevation: 8,
        },
        tabBarLabelStyle: { fontSize: 11, fontWeight: "500" },
        tabBarIconStyle: { marginBottom: -2 },
        tabBarIcon: ({ color, size }) => {
          let iconName: string = "help";
          if (route.name === "Dashboard") iconName = "grid";
          if (route.name === "Faturas") iconName = "document-text";
          if (route.name === "Empresas") iconName = "business";
          return <Ionicons name={iconName as any} size={size - 2} color={color} />;
        },
      })}
    >
      <Tab.Screen
        name="Dashboard"
        component={DashboardScreen}
        options={{ title: "Dashboard" }}
      />
      <Tab.Screen
        name="Faturas"
        component={FaturasScreen}
        options={{ title: "Faturas" }}
      />
      <Tab.Screen
        name="Empresas"
        component={EmpresasStack}
        options={{ title: "Empresas", headerShown: false }}
      />
    </Tab.Navigator>
  );
}
