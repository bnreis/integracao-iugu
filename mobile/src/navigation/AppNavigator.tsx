import React from "react";
import { TouchableOpacity, View } from "react-native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import DashboardScreen from "../screens/DashboardScreen";
import FaturasScreen from "../screens/FaturasScreen";
import EmpresasScreen from "../screens/EmpresasScreen";
import CadastrarEmpresaScreen from "../screens/CadastrarEmpresaScreen";
import ContaMenu from "../components/ContaMenu";

const Tab = createBottomTabNavigator();
const EmpresasStackNav = createNativeStackNavigator();

// Ordem das abas para o swipe horizontal.
const ORDEM_ABAS = ["Dashboard", "Faturas", "Empresas"];

// Envolve uma tela com um detector de gesto horizontal: ao "arrastar" (fling)
// para os lados, navega para a aba anterior/seguinte. Usa só o
// react-native-gesture-handler (já instalado) — sem libs novas.
// - activeOffsetX: só ativa em movimento claramente horizontal.
// - failOffsetY: desiste se o gesto for vertical (deixa a rolagem da lista livre).
// - exige velocidade + deslocamento mínimos (fling), pra não brigar com a barra
//   de filtros horizontal nem disparar sem querer.
function comSwipeAbas(Componente: React.ComponentType<any>, indice: number) {
  return function TelaComSwipe(props: any) {
    const navigation = useNavigation<any>();
    const irPara = (i: number) => {
      if (i < 0 || i >= ORDEM_ABAS.length) return;
      navigation.navigate(ORDEM_ABAS[i] as never);
    };
    const pan = Gesture.Pan()
      .runOnJS(true)
      .activeOffsetX([-30, 30])
      .failOffsetY([-20, 20])
      .onEnd((e) => {
        if (Math.abs(e.velocityX) < 500 || Math.abs(e.translationX) < 50) return;
        if (e.translationX < 0) irPara(indice + 1); // arrastou p/ esquerda → próxima
        else irPara(indice - 1); // arrastou p/ direita → anterior
      });
    return (
      <GestureDetector gesture={pan}>
        <View style={{ flex: 1 }}>
          <Componente {...props} />
        </View>
      </GestureDetector>
    );
  };
}

// Telas embrulhadas com o swipe (índice = posição em ORDEM_ABAS).
const DashboardComSwipe = comSwipeAbas(DashboardScreen, 0);
const FaturasComSwipe = comSwipeAbas(FaturasScreen, 1);

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

const EmpresasComSwipe = comSwipeAbas(EmpresasStack, 2);

interface AppNavigatorProps {
  onSwitch: (id: string) => void;
  onLogout: () => void;
}

export default function AppNavigator({ onSwitch, onLogout }: AppNavigatorProps) {
  // Insets do dispositivo: insets.bottom = altura da barra de navegação do
  // Android (gestos/botões). Somamos ao menu para ele não ficar por baixo dela.
  const insets = useSafeAreaInsets();
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
        // Menu de conta (trocar empresa / sair) no cabeçalho das abas.
        headerRight: () => <ContaMenu onSwitch={onSwitch} onLogout={onLogout} />,
        tabBarActiveTintColor: "#1a56db",
        tabBarInactiveTintColor: "#9ca3af",
        tabBarStyle: {
          // Reserva o espaço da barra do sistema embaixo (corrige o menu atrás
          // dos botões do celular). No mínimo 6px quando não há inset.
          paddingBottom: Math.max(insets.bottom, 6),
          paddingTop: 6,
          height: 58 + Math.max(insets.bottom, 0),
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
        component={DashboardComSwipe}
        options={{ title: "Dashboard" }}
      />
      <Tab.Screen
        name="Faturas"
        component={FaturasComSwipe}
        options={{ title: "Faturas" }}
      />
      <Tab.Screen
        name="Empresas"
        component={EmpresasComSwipe}
        options={{ title: "Empresas", headerShown: false }}
      />
    </Tab.Navigator>
  );
}
