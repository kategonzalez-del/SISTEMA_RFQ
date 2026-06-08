import json
import re
import os
from django.shortcuts import render
from django.http import JsonResponse
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

from .models import Material, DrawingAnalysis, DrawingDetectedMaterial, PartComponent
from .forms import DrawingUploadForm

#from RFQ.services.parsers.pdf_parser import (
#    extract_text_from_pdf, extract_volume, extract_weight, extract_color)
from RFQ.services.ai.structured_extractor import extract_rfq_data
from RFQ.services.materials.embedding_matcher import match_material

#from RFQ.services.cad.step_parser import analyze_step
#from RFQ.services.cad.stl_parser import analyze_stl
from django.db.models import Q

def upload_and_process_rfq(request):
    context = {}
    form = DrawingUploadForm()
    
    history_id = request.GET.get('history_id')
    if history_id:
        try:
            past_analysis = DrawingAnalysis.objects.get(id=history_id)
            detected_items = past_analysis.detected_materials.all()
            for item in detected_items:
                if not item.detected_material and item.detected_family != "N/D":
                    clean_family = item.detected_family.replace("Resin", "").strip()
                    item.sugerencias = Material.objects.filter(family__icontains=clean_family[:4])[:3]
                else:
                    item.sugerencias = None
            
            past_classifications = []
            for item in detected_items:
                if "Origen:" in item.bom_reference:
                    orig = item.bom_reference.split("Origen:")[-1].strip()
                    if orig not in past_classifications:
                        past_classifications.append(orig)
            
            context['analysis'] = past_analysis
            context['detected_components'] = detected_items
            context['file_classifications'] = past_classifications
            context['filename'] = past_analysis.uploaded_file.name if past_analysis.uploaded_file else "Archivo Histórico"
        except DrawingAnalysis.DoesNotExist:
            context['error'] = "El análisis histórico solicitado no existe."

    if request.method == 'POST' and request.FILES.getlist('rfq_file'):
        uploaded_files = request.FILES.getlist('rfq_file')
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'tmp'))
        
        primary_pdf = None
        all_saved_files = []
        
        for u_file in uploaded_files:
            filename = fs.save(u_file.name, u_file)
            file_path = fs.path(filename)
            ext = os.path.splitext(u_file.name)[1].lower()
            all_saved_files.append({'name': u_file.name, 'path': file_path, 'ext': ext})
            if ext == '.pdf' and not primary_pdf:
                primary_pdf = filename 

        if not primary_pdf and all_saved_files:
            primary_pdf = all_saved_files[0]['name']

       
        analysis = DrawingAnalysis.objects.create(
            uploaded_file=f"tmp/{primary_pdf}",
            raw_text="",
            gemini_raw_json={},
            status='processing'
        )

        return JsonResponse({
            'status': 'initiated',
            'analysis_id': analysis.id,
            'files': all_saved_files
        })

    context['all_materials_catalog'] = Material.objects.all().order_by('material_code')
    
    context['past_analyses'] = DrawingAnalysis.objects.all().order_by('-uploaded_at')[:20]
    context['form'] = form
    return render(request, 'RFQ/upload.html', context)

@csrf_exempt
def process_single_file_async(request):
    """
    ENDPOINT ASÍNCRONO BLINDADO: Procesa planos individuales cruzando volúmenes 3D
    específicos por cada número de parte para evitar la duplicación de pesos teóricos.
    Soporta metadata dual de pesos (gramos y libras) y vinculación por árbol de ensamble.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
        
    try:
        data = json.loads(request.body)
        analysis_id = data.get('analysis_id')
        file_name = data.get('file_name')
        file_path = data.get('file_path')
        ext = data.get('ext')
        is_subcomponent_manual = data.get('is_subcomponent', False)
        
        analysis = DrawingAnalysis.objects.get(id=analysis_id)
        parts_found_payload = []
        classification_string = f"{file_name}: 🧩 Subcomponente 2D" if is_subcomponent_manual else f"{file_name}: 🏢 Plano de Ensamble Maestro"

        if ext in ['.step', '.stp', '.stl']:
            try:
                if ext in ['.step', '.stp']:
                    from RFQ.services.cad.step_parser import analyze_step
                    threed_data = analyze_step(file_path)
                else:
                    from RFQ.services.cad.stl_parser import analyze_stl
                    threed_data = analyze_stl(file_path)
                
                volume_cm3 = threed_data.get('volume_cm3', 0)
                classification_string = f"{file_name}: 📐 Geometría 3D Indexada ({volume_cm3} cm³)"
                
                return JsonResponse({
                    'success': True,
                    'type': '3d',
                    'file_name': file_name,
                    'classification': classification_string,
                    'volume_cm3': volume_cm3,
                    'weight_grams': None,
                    'parts': []
                })
            except Exception as e:
                return JsonResponse({'success': False, 'error': f"Fallo en lectura 3D: {str(e)}"})

        # CASO 2: PLANOS TÉCNICOS 2D (.PDF)
        elif ext == '.pdf':
            from RFQ.services.parsers.pdf_parser import extract_text_from_pdf, extract_volume, extract_weight
            raw_text = extract_text_from_pdf(file_path)
            analysis.raw_text += f"\n--- ORIGEN: {file_name} ---\n" + raw_text
            
            clean_text_for_regex = re.sub(r'MASSE\s*:\s*WEIGHT', 'WEIGHT', raw_text, flags=re.IGNORECASE)
            local_volume = extract_volume(raw_text)
            local_weight = extract_weight(clean_text_for_regex) or extract_weight(raw_text)
            
            if local_volume and not analysis.estimated_volume:
                analysis.estimated_volume = local_volume
            if local_weight and not analysis.estimated_weight:
                analysis.estimated_weight = local_weight
            print(f"[DEBUG] Texto extraído ({len(raw_text)} chars): {raw_text[:300]}")

            analysis.save()
            
            try:
                gemini_result = extract_rfq_data(raw_text)
                raw_json = gemini_result.get('raw_response', '{}') if 'raw_response' in gemini_result else json.dumps(gemini_result)
                clean_json = re.sub(r'^```json\s*|```$', '', raw_json, flags=re.MULTILINE).strip()
                raw_data = json.loads(clean_json)
                print(f"[DEBUG] raw_data tipo: {type(raw_data)}, contenido: {str(raw_data)[:200]}")
            except Exception:
                raw_data = {}

            parts_list = []
            if not raw_data:
                file_pure_name = os.path.splitext(file_name)[0]
                parts_list = [{"part_number": file_pure_name, "description": "Componente extraído"}]
            elif isinstance(raw_data, list):
                parts_list = raw_data
            elif isinstance(raw_data, dict):
                parts_list = raw_data.get('parts', raw_data.get('part_numbers', [raw_data] if 'part_number' in raw_data else []))

            for part in parts_list:
                part_num = part.get('part_number') or part.get('part_number_base')
                part_desc = part.get('name') or part.get('description') or ''
                clean_part_key = str(part_num).strip().upper() if part_num else ""
                
                material_data = part.get('materials', [{}])[0] if part.get('materials') else part.get('material', {})
                if not isinstance(material_data, dict):
                    material_data = {"name": str(material_data)}
                    
                commercial_name_ia = material_data.get('material_name') or material_data.get('name') or ''
                resin_family_ia = material_data.get('resin_family') or material_data.get('family') or ''
                color_ia = material_data.get('color') or ''
                supplier_ia = material_data.get('supplier') or 'No especificado'
                
                alt_material_data = part.get('alternative_material_suggestions', [{}])[0] if part.get('alternative_material_suggestions') else {}
                alt_resin_name = alt_material_data.get('name', '')
                if not alt_resin_name or alt_resin_name.upper() == 'NULL':
                    alt_resin_name = 'Ninguna registrada'

                matched_material_db = None
                is_matched_via_alternate = False
                
                if isinstance(commercial_name_ia, str) and commercial_name_ia.strip():
                    match_result = match_material(commercial_name_ia)
                    if match_result and match_result["confidence"] >= 45:
                        matched_material_db = match_result["material"]
                
                if not matched_material_db and alt_resin_name and alt_resin_name != 'Ninguna registrada':
                    alt_match_result = match_material(alt_resin_name)
                    if alt_match_result and alt_match_result["confidence"] >= 45:
                        matched_material_db = alt_match_result["material"]
                        is_matched_via_alternate = True

                volume_val = part.get('volume_cm3') or part.get('volume')
                
                if not volume_val and clean_part_key:
                    threed_geom = DrawingDetectedMaterial.objects.filter(
                        Q(analysis=analysis) & 
                        Q(bom_reference__icontains="Geometría 3D Indexada") & 
                        Q(bom_reference__icontains=clean_part_key)
                    ).first()
                    if threed_geom:
                        volume_val = threed_geom.component_volumen

                if not volume_val:
                    volume_val = local_volume
                        
                weight_val = part.get('weight_grams') or part.get('weight') or local_weight
                data_source_flag = "Extraído de Plano PDF"

                if not weight_val and local_weight:
                    weight_val = local_weight
                    data_source_flag = "Extraído vía Regex PDF (Masse/Weight)"

                if volume_val and not weight_val:
                    density = matched_material_db.density if matched_material_db else 1.05
                    try:
                        weight_val = float(volume_val) * float(density)
                        data_source_flag = f"Peso Estimado ({volume_val} cm³ x {density} g/cm³)"
                    except:
                        weight_val = None

                secondary_components = part.get('secondary_embedded_components', [])
                secondary_text_list = []
                if secondary_components and weight_val:
                    for sub_item in secondary_components:
                        s_name = sub_item.get('component_name', '')
                        m_type = sub_item.get('material_type', '')
                        qty = sub_item.get('quantity', 1) or 1
                        added_weight = (2.5 * qty) if m_type == 'METAL' else (0.5 * qty)
                        weight_val = float(weight_val) + added_weight
                        secondary_text_list.append(f"{qty}x {s_name}")
                        data_source_flag = "Peso Compuesto (Resina + Insertos)"

                ref_secundarios = f" | Lleva: {', '.join(secondary_text_list)}" if secondary_text_list else ""

                db_component = DrawingDetectedMaterial.objects.create(
                    analysis=analysis,
                    part_number=part_num,
                    raw_material_text=commercial_name_ia if commercial_name_ia else part_desc,
                    detected_material=matched_material_db,
                    detected_family=resin_family_ia if resin_family_ia else "N/D",
                    detected_color=color_ia if color_ia else "N/D",
                    component_weight=round(float(weight_val), 2) if weight_val else None,
                    component_volumen=round(float(volume_val), 2) if volume_val else None,
                    bom_reference=f"{part_desc}{ref_secundarios} | [Alt: {alt_resin_name}] | [{data_source_flag}] | Origen: {file_name}"
                )

                if part_num:
                    PartComponent.objects.create(
                        parent=analysis,
                        child_part_number=part_num,
                        estimated_weight=weight_val if weight_val else 0,
                        quantity=part.get('quantity', 1)
                    )

                # CÁLCULO DUAL DE PESO EN LIBRAS (1 gramo = 0.00220462 libras)
                weight_lbs = round(float(weight_val) * 0.00220462, 4) if weight_val else None

                parts_found_payload.append({
                    'part_number': part_num or "Insumo / Adicional",
                    'description': part_desc,
                    'bom_reference': db_component.bom_reference,
                    'alternative_resin': alt_resin_name,
                    'data_source': data_source_flag,
                    'detected_material': matched_material_db.commercial_name if matched_material_db else None,
                    'material_code': matched_material_db.material_code if matched_material_db else None,
                    'raw_material_text': commercial_name_ia if commercial_name_ia else "No especificado",
                    'detected_family': db_component.detected_family,
                    'detected_color': db_component.detected_color,
                    'component_weight': db_component.component_weight,
                    'component_weight_lbs': weight_lbs, 
                    'component_volumen': db_component.component_volumen,
                    'color_completo': color_ia,
                    'is_matched_via_alternate': is_matched_via_alternate,
                    'secondary_elements': secondary_text_list,
                    'sugerencias': []
                })

            return JsonResponse({
                'success': True,
                'type': 'pdf',
                'file_name': file_name,
                'classification': classification_string,
                'parts': parts_found_payload
            })
    
    except Exception as general_err:
        return JsonResponse({'success': False, 'error': str(general_err)}, status=500)
@csrf_exempt
def finalize_analysis_status(request):
    """
    Consolida el estado final de la auditoría. Configura la nomenclatura específica
    multi-maestro e introduce el campo descriptivo final de los números de parte.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método inválido'}, status=400)
        
    try:
        data = json.loads(request.body)
        analysis_id = data.get('analysis_id')
        analysis = DrawingAnalysis.objects.get(id=analysis_id)
        detected_items = analysis.detected_materials.all()
        
        total_validos = 0
        archivos_maestros = []
        partes_sistema = []
        
        for item in detected_items:
            if item.component_weight or item.detected_material:
                total_validos += 1
                
            if item.part_number and item.part_number != "Insumo / Adicional":
                if item.part_number not in partes_sistema:
                    partes_sistema.append(item.part_number)
            
            if "Origen:" in item.bom_reference:
                orig_file = item.bom_reference.split("Origen:")[-1].split("|")[0].strip()
                if orig_file and orig_file not in archivos_maestros:
                    archivos_maestros.append(orig_file)

        if archivos_maestros:
            nombres_limpios = [os.path.splitext(f)[0] for f in archivos_maestros]
            nuevo_nombre = f"Planos: [{', '.join(nombres_limpios[:3])}]"
            if len(nombres_limpios) > 3:
                nuevo_nombre += "..."
        elif partes_sistema:
            nuevo_nombre = f"Partes: [{', '.join(partes_sistema[:3])}]"
        else:
            nuevo_nombre = f"Análisis Técnico Lote #{analysis.id}"

        analysis.material_text = nuevo_nombre
        analysis.raw_text = f"TITULO_LOTE: {nuevo_nombre}\n" + analysis.raw_text
        
        if total_validos == 0:
            analysis.status = 'failed'
        else:
            analysis.status = 'completed'
            
        analysis.save()
        return JsonResponse({
            'status': 'finalized',
            'final_status': analysis.status,
            'suggested_name': nuevo_nombre
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)