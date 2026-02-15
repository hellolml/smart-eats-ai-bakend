// import { createClient } from '@alipay/yfd-supabase';

// // 要做替换
// const client = createClient({
//     appId:
//         (window as any)?.__MUSE__?.yfdAppId ||
//         (window as any)?.parent?.__dbConfig__?.appId,
//     versionId: 'app-version-v1',
//     baseUrl: 'https://appio.antgroup-inc.cn'
// });

// export const apiService = {
//     async getIngredients() {
//         if (!client.isReady()) await client.waitForInitialization();
//         const { data, error } = await client
//             .from('ingredients')
//             .select('*')
//             .eq('is_deleted', false);
//         if (error) {
//             throw error;
//         }
//         return data;
//     },

//     async addIngredient(data: any) {
//         if (!client.isReady()) await client.waitForInitialization();
//         const { data: result, error } = await client
//             .from('ingredients')
//             .insert([{...data, id:Date.now().toString()}])
//             .select()
//             .single();
//         if (error) {
//             throw error;
//         }
//         return result;
//     },

//     async addHistory(data: any) {
//         if (!client.isReady()) await client.waitForInitialization();
//         const { data: result, error } = await client
//             .from('eating_histories')
//             .insert([{...data, id:Date.now().toString()}])
//             .select()
//             .single();
//         if (error) {
//             throw error;
//         }
//         return result;
//     },

//     async getUserProfile() {
//         if (!client.isReady()) await client.waitForInitialization();
//         const { data, error } = await client
//             .from('user')
//             .select('*')
//             .eq('id', 'default_user')
//             .single();
//         if (error) {
//             throw error;
//         }
//         return data;
//     },

//     async updateUserProfile(data: any) {
//         if (!client.isReady()) await client.waitForInitialization();
//         const { data: result, error } = await client
//             .from('user')
//             .update(data)
//             .eq('id', 'default_user')
//             .select()
//             .single();
//         if (error) {
//             throw error;
//         }
//         return result;
//     }
// };