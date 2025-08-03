import api from '../API/Index';
import { EntityRelationshipRule } from '../types';

const postProcessing = async (taskParam: string[], entityRelationshipRules?: EntityRelationshipRule[]) => {
  try {
    const responses = [];

    // Entity relationship task'ini ayır
    const entityTask = 'entity_relationship_post_processing';
    const hasEntityTask = taskParam.includes(entityTask);
    const otherTasks = taskParam.filter((task) => task !== entityTask);

    // Önce normal post-processing task'larını çalıştır
    if (otherTasks.length > 0) {
      const formData = new FormData();
      formData.append('tasks', JSON.stringify(otherTasks));

      const response = await api.post(`/post_processing`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
      responses.push(response);
    }

    // Sonra entity relationship post-processing'i çalıştır (eğer varsa ve rules varsa)
    if (hasEntityTask && entityRelationshipRules && entityRelationshipRules.length > 0) {
      const formData = new FormData();

      // Entity relationship rules'ları yeni frontend formatında gönder
      const backendRules = entityRelationshipRules.map((rule) => ({
        sourceNodeType: rule.sourceNodeType,
        targetNodeType: rule.targetNodeType,
        relationshipType: rule.relationshipType,
        removeExistingRelationships: rule.removeExistingRelationships,
      }));

      formData.append('post_processing_rules', JSON.stringify(backendRules));

      // Entity relationship endpoint'ini kullan
      const response = await api.post(`/entity_relationship_post_processing`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
      responses.push(response);
    } else if (hasEntityTask) {
      // Entity task seçildi ama rules yok - uyarı
      console.warn('Entity relationship post-processing task selected but no rules provided');
      throw new Error('Entity relationship post-processing requires rules to be configured');
    }

    // En son response'u döndür (çoğunlukla entity relationship response'u)
    return responses[responses.length - 1] || responses[0];
  } catch (error) {
    console.log('Error updating the graph:', error);
    throw error;
  }
};

export { postProcessing };
